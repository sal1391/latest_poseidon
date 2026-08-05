#!/usr/bin/env bash
# Poseidon EC2 deployment -- Phase 14 Task 6 (doc 07 section 5).
#
# IAM role + instance profile for the EC2 box: Bedrock invoke (the exact
# cross-region-inference ARN shape learned and proven on 2026-08-03 --
# docs/superpowers/plans/2026-08-03-aws-auth0-setup.task.md, Track A6/A7)
# plus S3 access scoped to the artifact bucket ONLY. Deliberately NO
# Secrets Manager statement -- this phase's posture is an on-box env file,
# not Secrets Manager (Carlos-confirmed; that arrives with the Snowflake
# credentials effort per doc 07 section 5's amendment).
#
# THIS SCRIPT CREATES ITS OWN POLICY OBJECT (PoseidonEc2InstancePolicy),
# rather than reusing or editing the existing PoseidonBedrockInvoke policy
# already attached to the poseidon-dev IAM user (2026-08-03, local
# development credentials). The Bedrock STATEMENT SHAPE is mirrored
# verbatim from that proven policy (cross-region inference-profile ARNs +
# the all-US-region foundation-model grant + the two aws-marketplace
# actions); the object itself is new and separate, because this policy
# additionally needs S3 access the dev user's policy has no reason to
# carry, and editing a policy a different, already-working principal
# depends on is exactly the blast-radius risk this script should avoid.
#
# NOTE ON --dry-run: IAM's mutating calls have no DryRun parameter, so this
# script's check-mode (DRY_RUN=1 or --dry-run) PRINTS the commands it would
# run instead of executing them. Every read-only lookup (get-role,
# list-policies, get-instance-profile, ...) still runs for real, which is
# what makes this script idempotent.
#
# Usage:
#   S3_BUCKET_NAME=<REPLACE: same bucket 03-s3.sh created> ./04-iam.sh [--dry-run]

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
DRY_RUN="${DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

: "${S3_BUCKET_NAME:?set S3_BUCKET_NAME to the artifact bucket 03-s3.sh created}"

ROLE_NAME="${ROLE_NAME:-poseidon-ec2-role}"
INSTANCE_PROFILE_NAME="${INSTANCE_PROFILE_NAME:-poseidon-ec2-profile}"
POLICY_NAME="${POLICY_NAME:-PoseidonEc2InstancePolicy}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

# The exact model ids backend/poseidon/config/models.yml's bedrock profile
# names (illustrative per that file's own header -- verify against the
# current console before a real deploy). Inference-profile ARNs are
# region+account scoped; the foundation-model statement below is
# deliberately account-agnostic and region-wildcarded, per the 2026-08-03
# finding that a cross-region inference profile can route to any US region
# depending on capacity.
BEDROCK_MODEL_IDS=(
  "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
  "us.anthropic.claude-opus-4-1-20250805-v1:0"
  "us.amazon.nova-lite-v1:0"
  "us.amazon.nova-micro-v1:0"
)

build_inference_profile_arns_json() {
  local arns=()
  local id
  for id in "${BEDROCK_MODEL_IDS[@]}"; do
    arns+=("\"arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:inference-profile/${id}\"")
  done
  local IFS=,
  echo "${arns[*]}"
}

INFERENCE_PROFILE_ARNS_JSON="$(build_inference_profile_arns_json)"

read -r -d '' TRUST_POLICY <<JSON || true
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

read -r -d '' PERMISSION_POLICY <<JSON || true
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvokeInferenceProfiles",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [${INFERENCE_PROFILE_ARNS_JSON}]
    },
    {
      "Sid": "BedrockInvokeFoundationModelsAllUsRegions",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "arn:aws:bedrock:*::foundation-model/*"
    },
    {
      "Sid": "BedrockMarketplaceSubscription",
      "Effect": "Allow",
      "Action": ["aws-marketplace:ViewSubscriptions", "aws-marketplace:Subscribe"],
      "Resource": "*"
    },
    {
      "Sid": "ArtifactBucketReadWrite",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::${S3_BUCKET_NAME}/*"
    },
    {
      "Sid": "ArtifactBucketList",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${S3_BUCKET_NAME}"
    }
  ]
}
JSON

# --- Role -------------------------------------------------------------------
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "${ROLE_NAME} already exists"
elif [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would create role ${ROLE_NAME}"
else
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "Poseidon EC2 instance role -- Bedrock + artifact-bucket S3, no long-lived keys" >/dev/null
  echo "created role ${ROLE_NAME}"
fi

# --- Policy (create, or converge content on an existing one) --------------
existing_policy_arn="$(aws iam list-policies --scope Local \
  --query "Policies[?PolicyName=='${POLICY_NAME}'].Arn | [0]" --output text)"

if [[ -n "$existing_policy_arn" && "$existing_policy_arn" != "None" ]]; then
  echo "${POLICY_NAME} already exists: ${existing_policy_arn}"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] would create a new policy version (converging content) and set it as default, pruning the oldest non-default version first if already at the 5-version cap"
  else
    version_count="$(aws iam list-policy-versions --policy-arn "$existing_policy_arn" \
      --query 'length(Versions)' --output text)"
    if [[ "$version_count" -ge 5 ]]; then
      # The backticks below are JMESPath's own literal syntax (a JSON
      # `false`), not shell command substitution; single quotes are what
      # keeps the shell from touching them at all.
      # shellcheck disable=SC2016
      oldest_version="$(aws iam list-policy-versions --policy-arn "$existing_policy_arn" \
        --query 'Versions[?IsDefaultVersion==`false`] | sort_by(@, &CreateDate)[0].VersionId' \
        --output text)"
      if [[ -n "$oldest_version" && "$oldest_version" != "None" ]]; then
        aws iam delete-policy-version --policy-arn "$existing_policy_arn" --version-id "$oldest_version"
        echo "pruned oldest non-default policy version: ${oldest_version}"
      fi
    fi
    aws iam create-policy-version --policy-arn "$existing_policy_arn" \
      --policy-document "$PERMISSION_POLICY" --set-as-default >/dev/null
    echo "updated ${POLICY_NAME} (new default version)"
  fi
  POLICY_ARN="$existing_policy_arn"
elif [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would create policy ${POLICY_NAME}"
  POLICY_ARN=""
else
  POLICY_ARN="$(aws iam create-policy --policy-name "$POLICY_NAME" \
    --policy-document "$PERMISSION_POLICY" --query 'Policy.Arn' --output text)"
  echo "created ${POLICY_NAME}: ${POLICY_ARN}"
fi

# --- Attach policy to role ---------------------------------------------------
if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would attach ${POLICY_NAME} to ${ROLE_NAME}"
elif [[ -n "${POLICY_ARN:-}" ]]; then
  aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$POLICY_ARN"
  echo "attached ${POLICY_NAME} to ${ROLE_NAME}"
fi

# --- Instance profile --------------------------------------------------------
if aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null 2>&1; then
  echo "${INSTANCE_PROFILE_NAME} already exists"
elif [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would create instance profile ${INSTANCE_PROFILE_NAME}"
else
  aws iam create-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null
  echo "created instance profile ${INSTANCE_PROFILE_NAME}"
  # A freshly created instance profile is not always immediately consistent
  # for a following add-role-to-instance-profile call -- the documented
  # AWS workaround for a transient "not found" right after create.
  sleep 5
fi

# A plain read: safe to attempt even in --dry-run (nothing was mutated
# above), and correctly comes back empty if the profile was only ever
# print-only "would create"d, never actually created.
current_role_in_profile="$(aws iam get-instance-profile \
  --instance-profile-name "$INSTANCE_PROFILE_NAME" \
  --query "InstanceProfile.Roles[?RoleName=='${ROLE_NAME}'].RoleName | [0]" \
  --output text 2>/dev/null || true)"

if [[ "$current_role_in_profile" == "$ROLE_NAME" ]]; then
  echo "${ROLE_NAME} already a member of ${INSTANCE_PROFILE_NAME} (skipping)"
elif [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would add ${ROLE_NAME} to ${INSTANCE_PROFILE_NAME}"
else
  aws iam add-role-to-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" \
    --role-name "$ROLE_NAME"
  echo "added ${ROLE_NAME} to ${INSTANCE_PROFILE_NAME}"
fi

echo
echo "ROLE_NAME=${ROLE_NAME}"
echo "INSTANCE_PROFILE_NAME=${INSTANCE_PROFILE_NAME}"
echo "POLICY_ARN=${POLICY_ARN:-<not created -- dry-run>}"

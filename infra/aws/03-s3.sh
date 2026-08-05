#!/usr/bin/env bash
# Poseidon EC2 deployment -- Phase 14 Task 6 (doc 07 section 5, doc 05 section 7).
#
# Provisions the S3 artifact bucket (PDF briefs): create-if-missing, a
# lifecycle rule expiring objects after RETENTION_ARTIFACT_DAYS (default 90,
# matching the doc 07 section 6 environment contract), and a full public-
# access block. Both the lifecycle rule and the public-access-block calls
# are plain PUTs against desired state -- naturally idempotent, always
# re-applied -- unlike the bucket itself, which is check-before-create.
#
# NOTE ON --dry-run: the S3 API has no DryRun parameter, so this script's
# check-mode (DRY_RUN=1 or --dry-run) PRINTS the exact command it would run
# for every mutating call instead of executing it. The bucket-existence
# lookup (head-bucket) still runs for real.
#
# Usage:
#   S3_BUCKET_NAME=<REPLACE: globally-unique bucket name, or leave unset to
#     resolve poseidon-artifacts-<account-id>> ./03-s3.sh [--dry-run]

set -euo pipefail

# Git Bash (MSYS) on Windows can rewrite a leading-slash argument into a
# Windows path before it reaches aws.exe -- see 05-ec2.sh's own comment for
# the full story and the three places it actually bites there. No-op on a
# real Linux shell; applied here too so an edit adding such an argument
# later does not have to rediscover the bug.
export MSYS_NO_PATHCONV=1

REGION="${AWS_REGION:-us-east-1}"
DRY_RUN="${DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

RETENTION_ARTIFACT_DAYS="${RETENTION_ARTIFACT_DAYS:-90}"

if [[ -z "${S3_BUCKET_NAME:-}" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  S3_BUCKET_NAME="poseidon-artifacts-${ACCOUNT_ID}"
  echo "S3_BUCKET_NAME not set; resolved: ${S3_BUCKET_NAME}"
fi

bucket_exists() {
  aws s3api head-bucket --region "$REGION" --bucket "$S3_BUCKET_NAME" >/dev/null 2>&1
}

if bucket_exists; then
  echo "${S3_BUCKET_NAME} already exists (skipping create)"
else
  echo "${S3_BUCKET_NAME} not found; creating"
  if [[ "$DRY_RUN" == "1" ]]; then
    if [[ "$REGION" == "us-east-1" ]]; then
      echo "[dry-run] would run: aws s3api create-bucket --region ${REGION} --bucket ${S3_BUCKET_NAME}"
    else
      echo "[dry-run] would run: aws s3api create-bucket --region ${REGION} --bucket ${S3_BUCKET_NAME} --create-bucket-configuration LocationConstraint=${REGION}"
    fi
  else
    # us-east-1 is the one region that REJECTS an explicit LocationConstraint
    # matching itself -- every other region requires one. Handled explicitly
    # rather than assumed, since these scripts default to us-east-1 but are
    # not hardcoded to only ever run there.
    if [[ "$REGION" == "us-east-1" ]]; then
      aws s3api create-bucket --region "$REGION" --bucket "$S3_BUCKET_NAME" >/dev/null
    else
      aws s3api create-bucket --region "$REGION" --bucket "$S3_BUCKET_NAME" \
        --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
    fi
    echo "created ${S3_BUCKET_NAME}"
  fi
fi

# --- Public access block: all four settings on, always re-applied ---------
if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would run: aws s3api put-public-access-block --region ${REGION} --bucket ${S3_BUCKET_NAME} --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
else
  aws s3api put-public-access-block --region "$REGION" --bucket "$S3_BUCKET_NAME" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "public access block: all four settings enabled on ${S3_BUCKET_NAME}"
fi

# --- Lifecycle: expire artifacts after RETENTION_ARTIFACT_DAYS -------------
LIFECYCLE_JSON=$(
  cat <<JSON
{
  "Rules": [
    {
      "ID": "poseidon-artifact-expiry",
      "Filter": {},
      "Status": "Enabled",
      "Expiration": { "Days": ${RETENTION_ARTIFACT_DAYS} }
    }
  ]
}
JSON
)

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would run: aws s3api put-bucket-lifecycle-configuration --region ${REGION} --bucket ${S3_BUCKET_NAME} --lifecycle-configuration '${LIFECYCLE_JSON}'"
else
  aws s3api put-bucket-lifecycle-configuration --region "$REGION" --bucket "$S3_BUCKET_NAME" \
    --lifecycle-configuration "$LIFECYCLE_JSON"
  echo "lifecycle rule: expire objects after ${RETENTION_ARTIFACT_DAYS} days on ${S3_BUCKET_NAME}"
fi

echo
echo "S3_BUCKET_NAME=${S3_BUCKET_NAME}"

#!/usr/bin/env bash
# Poseidon EC2 deployment -- Phase 14 Task 6 (doc 07 section 5).
#
# Two security groups, check-before-create so this script is safe to re-run:
#   poseidon-ec2-sg  -- 80/443 open to the world, 22 restricted to ADMIN_CIDR
#   poseidon-rds-sg  -- 5432 admitted ONLY from poseidon-ec2-sg (never a CIDR)
#
# No account id or credential is hardcoded anywhere in this file -- every
# account-specific value comes from an environment variable or a live AWS
# CLI lookup.
#
# --dry-run: every mutating call below (create-security-group,
# authorize-security-group-ingress) natively supports EC2's own --dry-run
# flag, so this script uses that rather than a print-only simulation --
# --dry-run makes AWS validate permissions and parameters for real and
# report "DryRunOperation" (would have succeeded) or "UnauthorizedOperation"
# without creating anything. Because nothing is actually created in
# dry-run mode, a security group that does not yet exist only gets its
# create call dry-run'd; its ingress rules are exercised for real (still
# under --dry-run) only once the group actually exists from a prior,
# non-dry-run pass.
#
# Usage:
#   ADMIN_CIDR=<REPLACE: your admin IP in CIDR form, e.g. 203.0.113.4/32> \
#   VPC_ID=<REPLACE: target VPC id, or leave unset to use the account's default VPC> \
#     ./01-security-groups.sh [--dry-run]

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
EC2_SG_NAME="${EC2_SG_NAME:-poseidon-ec2-sg}"
RDS_SG_NAME="${RDS_SG_NAME:-poseidon-rds-sg}"

DRY_RUN="${DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

: "${ADMIN_CIDR:?set ADMIN_CIDR to your admin IP in CIDR form, e.g. 203.0.113.4/32 -- this is the ONLY source 22/tcp is ever opened to}"

if [[ -z "${VPC_ID:-}" ]]; then
  VPC_ID="$(aws ec2 describe-vpcs --region "$REGION" \
    --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' --output text)"
  if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
    echo "ERROR: VPC_ID not set and no default VPC found in $REGION; set VPC_ID explicitly." >&2
    exit 1
  fi
  echo "VPC_ID not set; resolved default VPC: $VPC_ID"
fi

sg_id_by_name() {
  local name="$1"
  local id
  id="$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=${name}" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)"
  if [[ "$id" == "None" ]]; then
    id=""
  fi
  printf '%s' "$id"
}

# authorize-security-group-ingress fails with InvalidPermission.Duplicate on
# a rule that is already there -- treated as success (idempotent), any other
# failure is fatal.
authorize_ingress_idempotent() {
  local desc="$1"
  shift
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] would authorize: ${desc}"
    aws ec2 authorize-security-group-ingress --region "$REGION" --dry-run "$@" || true
    return
  fi
  local out
  if out="$(aws ec2 authorize-security-group-ingress --region "$REGION" "$@" 2>&1)"; then
    echo "authorized: ${desc}"
  elif [[ "$out" == *"InvalidPermission.Duplicate"* ]]; then
    echo "already authorized: ${desc} (skipping)"
  else
    echo "$out" >&2
    exit 1
  fi
}

ensure_ec2_sg() {
  EC2_SG_ID="$(sg_id_by_name "$EC2_SG_NAME")"
  if [[ -n "$EC2_SG_ID" ]]; then
    echo "${EC2_SG_NAME} already exists: ${EC2_SG_ID}"
    return
  fi
  echo "${EC2_SG_NAME} not found; creating"
  if [[ "$DRY_RUN" == "1" ]]; then
    aws ec2 create-security-group --region "$REGION" --dry-run \
      --group-name "$EC2_SG_NAME" \
      --description "Poseidon EC2 instance -- 80/443 public, 22 restricted to ADMIN_CIDR" \
      --vpc-id "$VPC_ID" || true
    echo "[dry-run] stopping here for ${EC2_SG_NAME} -- re-run without --dry-run to create it, then re-run this script to authorize its ingress rules"
    EC2_SG_ID=""
    return
  fi
  EC2_SG_ID="$(aws ec2 create-security-group --region "$REGION" \
    --group-name "$EC2_SG_NAME" \
    --description "Poseidon EC2 instance -- 80/443 public, 22 restricted to ADMIN_CIDR" \
    --vpc-id "$VPC_ID" --query 'GroupId' --output text)"
  echo "created ${EC2_SG_NAME}: ${EC2_SG_ID}"
}

ensure_rds_sg() {
  RDS_SG_ID="$(sg_id_by_name "$RDS_SG_NAME")"
  if [[ -n "$RDS_SG_ID" ]]; then
    echo "${RDS_SG_NAME} already exists: ${RDS_SG_ID}"
    return
  fi
  echo "${RDS_SG_NAME} not found; creating"
  if [[ "$DRY_RUN" == "1" ]]; then
    aws ec2 create-security-group --region "$REGION" --dry-run \
      --group-name "$RDS_SG_NAME" \
      --description "Poseidon RDS -- admits only the EC2 instance security group" \
      --vpc-id "$VPC_ID" || true
    echo "[dry-run] stopping here for ${RDS_SG_NAME} -- re-run without --dry-run to create it, then re-run this script to authorize its ingress rule"
    RDS_SG_ID=""
    return
  fi
  RDS_SG_ID="$(aws ec2 create-security-group --region "$REGION" \
    --group-name "$RDS_SG_NAME" \
    --description "Poseidon RDS -- admits only the EC2 instance security group" \
    --vpc-id "$VPC_ID" --query 'GroupId' --output text)"
  echo "created ${RDS_SG_NAME}: ${RDS_SG_ID}"
}

ensure_ec2_sg
if [[ -n "$EC2_SG_ID" ]]; then
  authorize_ingress_idempotent "80/tcp from 0.0.0.0/0 on ${EC2_SG_NAME}" \
    --group-id "$EC2_SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0
  authorize_ingress_idempotent "443/tcp from 0.0.0.0/0 on ${EC2_SG_NAME}" \
    --group-id "$EC2_SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0
  authorize_ingress_idempotent "22/tcp from ${ADMIN_CIDR} on ${EC2_SG_NAME}" \
    --group-id "$EC2_SG_ID" --protocol tcp --port 22 --cidr "$ADMIN_CIDR"
fi

ensure_rds_sg
if [[ -n "$RDS_SG_ID" && -n "$EC2_SG_ID" ]]; then
  authorize_ingress_idempotent "5432/tcp from ${EC2_SG_NAME} (${EC2_SG_ID}) on ${RDS_SG_NAME}" \
    --group-id "$RDS_SG_ID" --protocol tcp --port 5432 --source-group "$EC2_SG_ID"
elif [[ -n "$RDS_SG_ID" && -z "$EC2_SG_ID" ]]; then
  echo "NOTE: ${RDS_SG_NAME} exists but ${EC2_SG_NAME} does not yet (dry-run) -- its 5432 rule needs ${EC2_SG_NAME}'s real id, run again once that group is created."
fi

echo
echo "EC2_SG_ID=${EC2_SG_ID}"
echo "RDS_SG_ID=${RDS_SG_ID}"
echo "VPC_ID=${VPC_ID}"

#!/usr/bin/env bash
# Poseidon EC2 deployment -- Phase 14 Task 6 (doc 07 section 5, decision D17).
#
# Provisions the RDS Postgres 16 instance EC2 mode uses for app state (chat
# history, run log, feedback, user memory -- the identical schema local's
# docker-compose Postgres carries). db.t3.micro, automated backups ON,
# backup-retention-period 7, deletion protection ON: the owner-decided RPO
# of 24h (docs/architecture/07-infrastructure.md section 4) is satisfied by
# the daily automated-backup window; retention 7 gives a week of recovery
# points, not just the RPO floor.
#
# NOTE ON --dry-run: the RDS API has no DryRun parameter (unlike EC2's), so
# this script's check-mode (DRY_RUN=1 or --dry-run) PRINTS the exact
# create-db-subnet-group / create-db-instance command it would run instead
# of executing it. Every read-only "does this already exist" lookup below
# still runs for real -- that is what makes this script idempotent and safe
# to re-run.
#
# Usage:
#   DB_MASTER_PASSWORD=<REPLACE: a strong password -- never hardcode a real one> \
#   VPC_ID=<REPLACE: target VPC, or leave unset for the account default VPC> \
#     ./02-rds.sh [--dry-run]
#
# Requires 01-security-groups.sh to have run first (looks up poseidon-rds-sg
# by name). Required: DB_MASTER_PASSWORD. Everything else below has a sane
# default and nothing account-specific is hardcoded.

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

: "${DB_MASTER_PASSWORD:?set DB_MASTER_PASSWORD -- never hardcode a real password in a script or commit it}"

DB_INSTANCE_ID="${DB_INSTANCE_ID:-poseidon-db}"
DB_NAME="${DB_NAME:-poseidon}"
DB_MASTER_USERNAME="${DB_MASTER_USERNAME:-poseidon}"
# Fixed at db.t3.micro per this task's own spec -- unlike the EC2 compute
# instance type (05-ec2.sh), this class is not up for a t3.small revisit
# here; Task 7 owns any later resize decision.
DB_INSTANCE_CLASS="${DB_INSTANCE_CLASS:-db.t3.micro}"
DB_ALLOCATED_STORAGE="${DB_ALLOCATED_STORAGE:-20}"
# Verify a current 16.x minor version is still supported before running:
#   aws rds describe-db-engine-versions --region "$REGION" --engine postgres \
#     --query "DBEngineVersions[?starts_with(EngineVersion, '16.')].EngineVersion"
# AWS deprecates specific minor versions on its own schedule -- this default
# is illustrative, the same "verify current ids per provider console"
# posture backend/poseidon/config/models.yml's own header states.
DB_ENGINE_VERSION="${DB_ENGINE_VERSION:-16.4}"
BACKUP_RETENTION_PERIOD="${BACKUP_RETENTION_PERIOD:-7}"
RDS_SG_NAME="${RDS_SG_NAME:-poseidon-rds-sg}"
DB_SUBNET_GROUP_NAME="${DB_SUBNET_GROUP_NAME:-poseidon-db-subnet-group}"

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

RDS_SG_ID="$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=${RDS_SG_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [[ -z "$RDS_SG_ID" || "$RDS_SG_ID" == "None" ]]; then
  echo "ERROR: ${RDS_SG_NAME} not found in ${VPC_ID}; run 01-security-groups.sh first." >&2
  exit 1
fi
echo "using ${RDS_SG_NAME}: ${RDS_SG_ID}"

# --- DB subnet group (RDS needs >=2 subnets in different AZs, even for a
# single-AZ instance) -------------------------------------------------------
existing_subnet_group="$(aws rds describe-db-subnet-groups --region "$REGION" \
  --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
  --query 'DBSubnetGroups[0].DBSubnetGroupName' --output text 2>/dev/null || true)"

if [[ -n "$existing_subnet_group" && "$existing_subnet_group" != "None" ]]; then
  echo "${DB_SUBNET_GROUP_NAME} already exists (skipping create)"
else
  if [[ -n "${DB_SUBNET_IDS:-}" ]]; then
    IFS=',' read -r -a subnet_ids <<<"$DB_SUBNET_IDS"
  else
    # tr -d '\r' is load-bearing on Windows: aws.exe writes CRLF, and while
    # $(...) command substitution strips the CR under MSYS, mapfile reading a
    # process substitution does not -- the CR survives onto the LAST element
    # and RDS rejects it as "Input can't contain control characters".
    mapfile -t subnet_ids < <(aws ec2 describe-subnets --region "$REGION" \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
      --query 'Subnets[].SubnetId' --output text | tr -d '\r' | tr '\t' '\n')
  fi
  if [[ "${#subnet_ids[@]}" -lt 2 ]]; then
    echo "ERROR: need at least 2 subnets (in different AZs) in ${VPC_ID}; set DB_SUBNET_IDS explicitly (comma-separated)." >&2
    exit 1
  fi
  echo "creating ${DB_SUBNET_GROUP_NAME} with subnets: ${subnet_ids[*]}"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] would run: aws rds create-db-subnet-group --region ${REGION} --db-subnet-group-name ${DB_SUBNET_GROUP_NAME} --db-subnet-group-description 'Poseidon RDS subnet group' --subnet-ids ${subnet_ids[*]}"
  else
    aws rds create-db-subnet-group --region "$REGION" \
      --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
      --db-subnet-group-description "Poseidon RDS subnet group" \
      --subnet-ids "${subnet_ids[@]}" >/dev/null
    echo "created ${DB_SUBNET_GROUP_NAME}"
  fi
fi

# --- The DB instance itself -------------------------------------------------
existing_instance="$(aws rds describe-db-instances --region "$REGION" \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --query 'DBInstances[0].DBInstanceIdentifier' --output text 2>/dev/null || true)"

if [[ -n "$existing_instance" && "$existing_instance" != "None" ]]; then
  echo "${DB_INSTANCE_ID} already exists (skipping create):"
  aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DB_INSTANCE_ID" \
    --query 'DBInstances[0].{Status:DBInstanceStatus,Endpoint:Endpoint.Address}' --output table
  exit 0
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would run: aws rds create-db-instance --region ${REGION} --db-instance-identifier ${DB_INSTANCE_ID} --db-name ${DB_NAME} --engine postgres --engine-version ${DB_ENGINE_VERSION} --db-instance-class ${DB_INSTANCE_CLASS} --allocated-storage ${DB_ALLOCATED_STORAGE} --master-username ${DB_MASTER_USERNAME} --master-user-password '<redacted>' --vpc-security-group-ids ${RDS_SG_ID} --db-subnet-group-name ${DB_SUBNET_GROUP_NAME} --backup-retention-period ${BACKUP_RETENTION_PERIOD} --deletion-protection --no-publicly-accessible --no-multi-az --storage-type gp3 --storage-encrypted"
  exit 0
fi

echo "creating ${DB_INSTANCE_ID} (this takes several minutes)"
aws rds create-db-instance --region "$REGION" \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --db-name "$DB_NAME" \
  --engine postgres \
  --engine-version "$DB_ENGINE_VERSION" \
  --db-instance-class "$DB_INSTANCE_CLASS" \
  --allocated-storage "$DB_ALLOCATED_STORAGE" \
  --master-username "$DB_MASTER_USERNAME" \
  --master-user-password "$DB_MASTER_PASSWORD" \
  --vpc-security-group-ids "$RDS_SG_ID" \
  --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
  --backup-retention-period "$BACKUP_RETENTION_PERIOD" \
  --deletion-protection \
  --no-publicly-accessible \
  --no-multi-az \
  --storage-type gp3 \
  --storage-encrypted >/dev/null

echo "create-db-instance accepted. Poll status with:"
echo "  aws rds describe-db-instances --region ${REGION} --db-instance-identifier ${DB_INSTANCE_ID} --query 'DBInstances[0].{Status:DBInstanceStatus,Endpoint:Endpoint.Address}'"
echo "Build /etc/poseidon/backend.env's DATABASE_URL from that endpoint once status=available:"
echo "  postgresql+psycopg://${DB_MASTER_USERNAME}:<password>@<endpoint>:5432/${DB_NAME}"

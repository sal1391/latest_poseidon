#!/usr/bin/env bash
# Poseidon EC2 deployment -- Phase 14 Task 6 (doc 07 section 5).
#
# Launches the EC2 instance the app runs on: an Amazon Linux 2023 AMI
# (resolved live via SSM's public parameter, never hardcoded -- AMI ids are
# region- and time-specific), the poseidon-ec2-role instance profile
# (04-iam.sh), the poseidon-ec2-sg security group (01-security-groups.sh),
# an Elastic IP, and a user-data script that installs docker + the compose
# plugin and creates a 2G swapfile.
#
# INSTANCE_TYPE defaults to t3.small -- doc 07's original t3.micro
# recommendation predates the worker container (Phase 13's memory-
# distillation job now runs as a second, always-on process on the same
# box); t3.small is this task's recommendation given that, but t3.micro
# remains a valid, cheaper choice -- Carlos decides in Task 7 by exporting
# INSTANCE_TYPE=t3.micro if he wants the original size.
#
# --dry-run: run-instances/allocate-address/associate-address all natively
# support EC2's own --dry-run flag, used below for the same reason
# 01-security-groups.sh uses it -- real permission/parameter validation with
# nothing actually created.
#
# Usage:
#   KEY_NAME=<REPLACE: an existing EC2 key pair name> \
#   SUBNET_ID=<REPLACE: a public subnet id, or leave unset to resolve one
#     with MapPublicIpOnLaunch=true in VPC_ID> \
#     ./05-ec2.sh [--dry-run]

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
DRY_RUN="${DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

: "${KEY_NAME:?set KEY_NAME to an existing EC2 key pair name -- 22/tcp is restricted to ADMIN_CIDR by 01-security-groups.sh, and SSH needs a key to log in through it}"

INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
INSTANCE_NAME="${INSTANCE_NAME:-poseidon-ec2}"
EIP_NAME="${EIP_NAME:-poseidon-ec2-eip}"
SWAP_SIZE_MB="${SWAP_SIZE_MB:-2048}"
ROOT_VOLUME_SIZE_GB="${ROOT_VOLUME_SIZE_GB:-20}"
EC2_SG_NAME="${EC2_SG_NAME:-poseidon-ec2-sg}"
INSTANCE_PROFILE_NAME="${INSTANCE_PROFILE_NAME:-poseidon-ec2-profile}"

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

if [[ -z "${SUBNET_ID:-}" ]]; then
  SUBNET_ID="$(aws ec2 describe-subnets --region "$REGION" \
    --filters "Name=vpc-id,Values=${VPC_ID}" "Name=map-public-ip-on-launch,Values=true" \
    --query 'Subnets[0].SubnetId' --output text)"
  if [[ -z "$SUBNET_ID" || "$SUBNET_ID" == "None" ]]; then
    echo "ERROR: SUBNET_ID not set and no public subnet (MapPublicIpOnLaunch=true) found in ${VPC_ID}; set SUBNET_ID explicitly." >&2
    exit 1
  fi
  echo "SUBNET_ID not set; resolved public subnet: ${SUBNET_ID}"
fi

if [[ -z "${AMI_ID:-}" ]]; then
  AMI_ID="$(aws ssm get-parameters --region "$REGION" \
    --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --query 'Parameters[0].Value' --output text)"
  echo "AMI_ID not set; resolved current Amazon Linux 2023 (x86_64): ${AMI_ID}"
fi

EC2_SG_ID="$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=${EC2_SG_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [[ -z "$EC2_SG_ID" || "$EC2_SG_ID" == "None" ]]; then
  echo "ERROR: ${EC2_SG_NAME} not found in ${VPC_ID}; run 01-security-groups.sh first." >&2
  exit 1
fi
echo "using ${EC2_SG_NAME}: ${EC2_SG_ID}"

if ! aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null 2>&1; then
  echo "ERROR: ${INSTANCE_PROFILE_NAME} not found; run 04-iam.sh first." >&2
  exit 1
fi

USERDATA_FILE="$(mktemp)"
trap 'rm -f "$USERDATA_FILE"' EXIT

# Amazon Linux 2023 user-data: docker + the compose plugin (installed as a
# CLI plugin binary rather than assumed to be in the distro repos, so this
# works the same way regardless of exactly which AL2023 image is current)
# + a swapfile. t3.small carries 2GiB RAM; the first-boot alembic migration
# + synthetic seed can spike without one.
cat >"$USERDATA_FILE" <<USERDATA
#!/bin/bash
set -euo pipefail

dnf update -y
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \\
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

fallocate -l ${SWAP_SIZE_MB}M /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

mkdir -p /etc/poseidon
echo "poseidon user-data complete" > /var/log/poseidon-userdata.log
USERDATA

existing_instance_id="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=${INSTANCE_NAME}" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[0].InstanceId | [0]' --output text)"

if [[ -n "$existing_instance_id" && "$existing_instance_id" != "None" ]]; then
  echo "${INSTANCE_NAME} already exists: ${existing_instance_id} (skipping launch)"
  INSTANCE_ID="$existing_instance_id"
else
  if [[ "$DRY_RUN" == "1" ]]; then
    aws ec2 run-instances --region "$REGION" --dry-run \
      --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" \
      --security-group-ids "$EC2_SG_ID" --subnet-id "$SUBNET_ID" \
      --iam-instance-profile "Name=${INSTANCE_PROFILE_NAME}" \
      --user-data "file://${USERDATA_FILE}" \
      --block-device-mappings "[{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":${ROOT_VOLUME_SIZE_GB},\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${INSTANCE_NAME}}]" \
      --count 1 || true
    echo "[dry-run] stopping here -- re-run without --dry-run to launch, then re-run this script to allocate/associate the Elastic IP"
    INSTANCE_ID=""
  else
    echo "launching ${INSTANCE_NAME} (${INSTANCE_TYPE}, ${AMI_ID})"
    INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" \
      --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" \
      --security-group-ids "$EC2_SG_ID" --subnet-id "$SUBNET_ID" \
      --iam-instance-profile "Name=${INSTANCE_PROFILE_NAME}" \
      --user-data "file://${USERDATA_FILE}" \
      --block-device-mappings "[{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":${ROOT_VOLUME_SIZE_GB},\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${INSTANCE_NAME}}]" \
      --count 1 --query 'Instances[0].InstanceId' --output text)"
    echo "launched: ${INSTANCE_ID}"
  fi
fi

# --- Elastic IP: allocate (tagged, check-before-create) then associate ----
existing_eip_alloc_id="$(aws ec2 describe-addresses --region "$REGION" \
  --filters "Name=tag:Name,Values=${EIP_NAME}" \
  --query 'Addresses[0].AllocationId' --output text)"

if [[ -n "$existing_eip_alloc_id" && "$existing_eip_alloc_id" != "None" ]]; then
  echo "${EIP_NAME} already exists: ${existing_eip_alloc_id}"
  ALLOC_ID="$existing_eip_alloc_id"
elif [[ "$DRY_RUN" == "1" ]]; then
  aws ec2 allocate-address --region "$REGION" --domain vpc --dry-run || true
  echo "[dry-run] would tag the allocated address Name=${EIP_NAME}"
  ALLOC_ID=""
else
  ALLOC_ID="$(aws ec2 allocate-address --region "$REGION" --domain vpc \
    --query 'AllocationId' --output text)"
  aws ec2 create-tags --region "$REGION" --resources "$ALLOC_ID" \
    --tags "Key=Name,Value=${EIP_NAME}"
  echo "allocated ${EIP_NAME}: ${ALLOC_ID}"
fi

if [[ -n "${INSTANCE_ID:-}" && -n "${ALLOC_ID:-}" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    aws ec2 associate-address --region "$REGION" --dry-run \
      --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID" || true
  else
    aws ec2 associate-address --region "$REGION" \
      --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID" >/dev/null
    public_ip="$(aws ec2 describe-addresses --region "$REGION" \
      --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp' --output text)"
    echo "associated ${ALLOC_ID} with ${INSTANCE_ID} -- public IP: ${public_ip}"
    echo "Point POSEIDON_DOMAIN's DNS record at ${public_ip} before running infra/runbooks/deploy-ec2.md's Caddy step."
  fi
else
  echo "NOTE: instance and/or Elastic IP not both available yet (dry-run, or launch just accepted) -- re-run this script once both exist to associate them."
fi

echo
echo "INSTANCE_ID=${INSTANCE_ID:-<pending>}"
echo "EIP_ALLOCATION_ID=${ALLOC_ID:-<pending>}"

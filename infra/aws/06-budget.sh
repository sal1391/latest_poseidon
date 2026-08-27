#!/usr/bin/env bash
# Poseidon EC2 deployment -- Phase 14 Task 6 (doc 07 section 7 "Cost
# guardrails").
#
# Verification-only: the AWS Budget alert already exists (created
# 2026-08-03 -- docs/superpowers/plans/2026-08-03-aws-auth0-setup.task.md,
# Track A5). This script does NOT create a duplicate budget -- it prints
# the account's current budgets and their notification subscribers so
# Task 7 can confirm the alert is still there and still sane before
# standing up EC2 spend.
#
# Every call this script makes is read-only (describe/list), so --dry-run
# is a no-op besides printing a notice.
#
# Usage: ./06-budget.sh [--dry-run]

set -euo pipefail

# Git Bash (MSYS) on Windows can rewrite a leading-slash argument into a
# Windows path before it reaches aws.exe -- see 05-ec2.sh's own comment for
# the full story and the three places it actually bites there. No-op on a
# real Linux shell; applied here too so an edit adding such an argument
# later does not have to rediscover the bug.
export MSYS_NO_PATHCONV=1

DRY_RUN="${DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi
if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] this script is read-only already (list/describe calls); nothing to simulate"
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "Budgets on account ${ACCOUNT_ID}:"
aws budgets describe-budgets --account-id "$ACCOUNT_ID" \
  --query 'Budgets[].{Name:BudgetName,Limit:BudgetLimit.Amount,Unit:BudgetLimit.Unit,TimeUnit:TimeUnit,Actual:CalculatedSpend.ActualSpend.Amount}' \
  --output table

# tr -d '\r': same Windows CRLF hazard documented in 02-rds.sh's subnet lookup.
# Here it would fail SILENTLY rather than loudly -- a trailing CR on the last
# budget name makes the later --budget-name lookup miss, which reads as "no
# such budget" and invites creating a duplicate.
mapfile -t budget_names < <(aws budgets describe-budgets --account-id "$ACCOUNT_ID" \
  --query 'Budgets[].BudgetName' --output text | tr -d '\r' | tr '\t' '\n')

if [[ "${#budget_names[@]}" -eq 0 ]]; then
  echo "WARNING: no budgets found on this account -- the 2026-08-03 alert may have been created under a different account/profile, or since removed. Verify before proceeding; this script deliberately does not create one to avoid a duplicate." >&2
  exit 0
fi

echo
echo "Notification subscribers per budget:"
for name in "${budget_names[@]}"; do
  echo "-- ${name} --"
  aws budgets describe-notifications-for-budget --account-id "$ACCOUNT_ID" --budget-name "$name" \
    --query 'Notifications[].{Type:NotificationType,Threshold:Threshold,ComparisonOperator:ComparisonOperator}' \
    --output table
done

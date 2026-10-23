# Cortex live proof: build signoff

Return this file, filled in, with the attachments listed at the bottom. Instructions for each
step are in `docs/snowflake/CORTEX_HANDOFF.md` (same folder; section numbers below refer to it).

## Environment

- Date:
- Account region:
- Cortex model ids used (default `claude-sonnet-5-5` and `claude-haiku-5-5`; list any `LLM_MODEL_<ROLE>` override and why):
- Token type (`PROGRAMMATIC_ACCESS_TOKEN` / `KEYPAIR_JWT` / `OAUTH` inside SPCS):
- Snowflake version (if known):
- Repo commit tested (`git rev-parse --short HEAD`):
- Signer name and role:

## Steps

- [ ] **Step A: offline parity** (section 5; expected last line `47 passed, 2 deselected`)
  - Command run:
  - Last line of output:
  - Pass / fail:
  - Notes:
- [ ] **Step B: two live smokes** (section 6; expected `2 passed`)
  - Command run:
  - Last line of output:
  - Pass / fail:
  - Notes (if `between_tools` was rejected, paste the 400 body and stop):
- [ ] **Step C: routing cases live** (section 7; not runnable against Cortex today, the executor is Bedrock-only)
  - Command run: not applicable
  - Last line of output: not applicable
  - Pass / fail: skipped, test is Bedrock-only
  - Notes:
- [ ] **Step D: one report in the running app** (section 8)
  - Command run:
  - Last line of output (the proof line under the report):
  - Pass / fail (narrative is the AI narrative and the proof line says `grounded`):
  - Notes:

## Attachments

- Fixtures attached (real `plain_completion` and `tool_call` response bodies, token redacted; section 10): yes / no
- Failure captures attached, if any (section 9): yes / no / not needed

## Sign-off

- [ ] Steps A, B and D passed and Step C was recorded as not applicable.

Signed: ____________________   Role: ____________________   Date: ____________

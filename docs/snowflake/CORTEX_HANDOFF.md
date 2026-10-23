# Cortex live proof: handoff for the Snowflake-side owner

You do not need to have opened this repository before. Follow the steps in order, paste the
last line of each output into `CORTEX_BUILD_SIGNOFF.md` (same folder), and return that file.

## 1. What this proves and who runs it

Poseidon is a chat app for marine fuel sales. Its language-model calls go through a "provider"
layer. One provider, `CortexProvider`, calls Claude **inside Snowflake** through Cortex's
Anthropic-compatible Messages endpoint:

    POST https://<account_identifier>.snowflakecomputing.com/api/v2/cortex/v1/messages

The repository's tests prove the request and response translation **offline**, against recorded
bodies. They cannot prove that your account accepts the request. This document is that live
proof. It is run by the Snowflake-side owner and is complete when
`docs/snowflake/CORTEX_BUILD_SIGNOFF.md` is returned signed.

Nothing here writes to Snowflake. The runs send a few small prompts to Cortex and read the
answers. Expected cost: a handful of small model calls (Step B: two; Step D: a few).

## 2. Prerequisites

1. A Snowflake account whose region offers **`claude-sonnet-5-5`** and **`claude-haiku-5-5`**
   under Cortex. Snowflake's "Cortex LLM REST API" page lists both; some ids are marked preview
   and are region-dependent. Check with `SELECT * FROM SNOWFLAKE.CORTEX.AVAILABLE_MODELS;` or the
   account's Cortex model availability page. Cross-region inference may need an admin to enable
   it. If your region lacks them, pick an available Claude id and set the override variables
   `LLM_MODEL_SYNTHESIS`, `LLM_MODEL_ROUTER`, `LLM_MODEL_MEMORY`, `LLM_MODEL_UTILITY` and
   `LLM_MODEL_MICRO` to it (one per role; the same value is fine), then record which id you used
   in the signoff. Note: the repo's configuration was written for the Claude 5.5 generation
   (`thinking` on, no `temperature`); an older Claude id may behave differently, and that
   difference is itself a finding to record.
2. A role granted `SNOWFLAKE.CORTEX_USER`.
3. A user who can authenticate with a **programmatic access token** (PAT). The authentication
   policy's `AUTHENTICATION_METHODS` must include `PROGRAMMATIC_ACCESS_TOKEN`, and the user must
   be subject to a network policy.
4. A clone of this repository at the PR C merge commit, with the Python environment built at
   `backend/.venv`. Setup is in `backend/README.md` (it creates the venv and runs
   `pip install -e ".[dev]"`). The repo is locked to Python 3.14 (`requires-python >=3.11`).
   The commands below use `backend/.venv/Scripts/python.exe` (Windows); on macOS or Linux use
   `backend/.venv/bin/python`. Never use a bare `python`: another interpreter on the machine will
   fail with a confusing `ModuleNotFoundError`.

## 3. Mint a token (outside SPCS)

Run in a Snowsight worksheet or SnowSQL as a role allowed to alter the user (this is the SQL
from Snowflake's "programmatic access tokens" page):

```sql
ALTER USER IF EXISTS <user> ADD PROGRAMMATIC ACCESS TOKEN poseidon_cortex_token
  ROLE_RESTRICTION = '<role>' DAYS_TO_EXPIRY = 10;
```

The statement returns the token secret **once**. Copy it immediately; Snowflake cannot show it
again. Default expiry is 15 days and the maximum is 365, unless an admin lowered it.

Inside SPCS (Snowpark Container Services) there is nothing to mint: the container reads the
OAuth token from the file `/snowflake/session/token` (refreshed every few minutes, each token
valid for up to an hour) and the host name from `SNOWFLAKE_HOST`. The provider re-reads that
file on every call. Steps A to D below assume the outside-SPCS path.

## 4. Environment variables

Set these in the shell you will run the commands from. None of them is ever logged, and the
token never appears in an error text (a test pins this).

| Variable | Value | Needed for |
|---|---|---|
| `SNOWFLAKE_ACCOUNT_URL` | `https://<account_identifier>.snowflakecomputing.com` | B, D (the live tests skip without it) |
| `SNOWFLAKE_CORTEX_TOKEN` | the PAT secret from section 3 | B, D |
| `SNOWFLAKE_CORTEX_TOKEN_TYPE` | `PROGRAMMATIC_ACCESS_TOKEN` (or `KEYPAIR_JWT` if you use a key-pair JWT) | B, D |
| `DATABASE_URL` | `postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon` | the app's Settings require it (the live tests fill in a placeholder) |
| `S3_BUCKET` | `poseidon-artifacts` | same |

PowerShell:

```powershell
$env:SNOWFLAKE_ACCOUNT_URL = "https://<account_identifier>.snowflakecomputing.com"
$env:SNOWFLAKE_CORTEX_TOKEN = "<secret>"
$env:SNOWFLAKE_CORTEX_TOKEN_TYPE = "PROGRAMMATIC_ACCESS_TOKEN"
$env:DATABASE_URL = "postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon"
$env:S3_BUCKET = "poseidon-artifacts"
```

bash:

```bash
export SNOWFLAKE_ACCOUNT_URL=https://<account_identifier>.snowflakecomputing.com
export SNOWFLAKE_CORTEX_TOKEN='<secret>'
export SNOWFLAKE_CORTEX_TOKEN_TYPE=PROGRAMMATIC_ACCESS_TOKEN
export DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon
export S3_BUCKET=poseidon-artifacts
```

Do **not** export `LLM_PROFILE` or `LLM_MODE`. The live tests of Step B set them themselves, and
Step D's compose override sets them for the backend; exporting them earlier only risks changing
the offline run of Step A.

If `SNOWFLAKE_ACCOUNT_URL` is unset the provider falls back to `https://$SNOWFLAKE_HOST`
(the SPCS case). If `SNOWFLAKE_CORTEX_TOKEN_TYPE` is unset, the type header is simply omitted
(Snowflake then detects the type itself).

## 5. Step A: offline parity (no Snowflake needed)

In every command below, `<path-to-clone>` is the directory where you cloned this repository.
Each block starts with its own `cd` to an absolute location, so the blocks run correctly in
sequence whatever directory the previous one left you in.

All `pytest` commands here are redirected to a file and never piped (a pipe hides a failing
exit code). The bash form works in Git Bash on Windows; the `env -u` part unsets a key that
would otherwise trigger paid calls elsewhere in the suite.

```bash
cd <path-to-clone>/backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' tests/test_provider_parity.py tests/test_llm_cortex.py > parity.txt 2>&1
tail -n 3 parity.txt
```

PowerShell equivalent:

```powershell
cd <path-to-clone>backend
Remove-Item Env:PERPLEXITY_API_KEY -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -m "not pg and not minio and not pdf and not router_live and not research_live" tests/test_provider_parity.py tests/test_llm_cortex.py *> parity.txt
Get-Content parity.txt -Tail 3
```

**Pass:** the last line reads `47 passed, 2 deselected` (recorded on the branch on 2026-10-23).
The two deselected tests are the two live Cortex smokes of Step B, which this step deliberately
excludes. Anything else means the checkout is not the PR C merge commit or the environment is
wrong; stop and report it.

## 6. Step B: two live smokes

```bash
cd <path-to-clone>/backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m router_live tests/test_llm_cortex.py > cortex-live.txt 2>&1
tail -n 5 cortex-live.txt
```

PowerShell equivalent:

```powershell
cd <path-to-clone>backend
Remove-Item Env:PERPLEXITY_API_KEY -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -m router_live tests/test_llm_cortex.py *> cortex-live.txt
Get-Content cortex-live.txt -Tail 5
```

Always name the file (`tests/test_llm_cortex.py`). A bare `-m router_live` would also run the
live tests of other providers.

**Pass:** `2 passed`. Without the variables of section 4 the two tests are skipped, so a
`2 skipped` line is not a pass.

- `test_cortex_utility_role_live_smoke`: one **Haiku 5.5** call through the utility role. It
  checks the reply is non-empty and the token counts are positive, never the wording.
- `test_cortex_forced_narrative_live_smoke`: the report-narrative call through the synthesis
  role on **Sonnet 5.5**. The provider sends `tool_choice: auto` plus a system instruction
  naming the tool, with `thinking: between_tools` (the 5.5 generation rejects native forcing).
  Pass means the model answered with exactly one `reporting.narrate` tool call.

If Cortex rejects `between_tools` (HTTP 400, `invalid_request_error`), capture the response body
(section 9) and **stop**. Whether to fall back to adaptive thinking is a decision for the repo
owner, not the runner.

## 7. Step C: the routing cases live (not runnable against Cortex today)

The plan for this proof included an optional thirteen-call routing run. On the PR C branch that
run **cannot** exercise Cortex: `test_live_routing_case` in `backend/tests/test_llm_loop.py`
is hard-wired to the Bedrock provider (`LLM_PROFILE="bedrock"`, `BedrockProvider`) and skips
unless AWS credentials are present. Running `-m router_live tests/test_llm_loop.py` on your
machine would either skip (no AWS credentials, the normal case) or call Bedrock, which is not
what you are proving.

**Skip this step** and write "not applicable, test is Bedrock-only" in the signoff. Making that
executor provider-aware is a repo change for the repo owner (follow-up).

## 8. Step D: one report in the running app

This shows the whole chain: a browser click, the app's report engine, and a Cortex-written
narrative. It needs Docker Desktop and Node.

1. Create `infra/docker-compose.cortex.yml` (do not commit it) so the backend uses Cortex. The
   base file hard-codes `LLM_PROFILE: bedrock` and `LLM_MODE: stub` for the `backend` service,
   so exporting variables alone is not enough; this override replaces them. The `${...}` values
   are read from your shell (section 4):

   ```yaml
   services:
     backend:
       environment:
         LLM_PROFILE: cortex
         LLM_MODE: live
         CHAT_MODE: live
         SNOWFLAKE_ACCOUNT_URL: ${SNOWFLAKE_ACCOUNT_URL}
         SNOWFLAKE_CORTEX_TOKEN: ${SNOWFLAKE_CORTEX_TOKEN}
         SNOWFLAKE_CORTEX_TOKEN_TYPE: ${SNOWFLAKE_CORTEX_TOKEN_TYPE}
   ```

   (`CHAT_MODE: live` is already set in the base file; it is repeated so the override is
   self-explanatory.)
2. Start the stack from the repo root:

   ```bash
   cd <path-to-clone>
   docker compose -f infra/docker-compose.yml -f infra/docker-compose.cortex.yml up -d
   ```

   If you have run this stack before with an older database, start fresh with
   `docker compose -f infra/docker-compose.yml -f infra/docker-compose.cortex.yml down -v` first.
3. Start the web app (the chat UI) in a second terminal, in `web/`:

   ```powershell
   cd <path-to-clone>\web
   $env:POSEIDON_INTERNAL_TOKEN = "dev-internal-token"
   $env:DRIZZLE_DATABASE_URL = "postgresql://poseidon:poseidon@localhost:5432/poseidon"
   npm install
   npm run dev
   ```

   bash: `cd <path-to-clone>/web`, `export POSEIDON_INTERNAL_TOKEN=dev-internal-token` and
   `export DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5432/poseidon`, then
   `npm install && npm run dev`. Open `http://localhost:3000`.
4. In the browser, start a new chat, then: click **Monthly report**; pick **Supplier office**;
   click an office chip; click the latest month chip. The full six-step browser checklist
   (including the PDF download and the follow-up questions) is the "Owner gate" section of
   `docs/superpowers/plans/2026-10-16-phase-17-prA2-report-in-chat.md`, which also describes
   what a passing gate looks like; steps 1 and 2 there are all this proof needs.

**Pass:** the report renders and its narrative section is the AI narrative, not the fixed
template text. The proof line under the report reads `narrative: synthesis role, prompt ... grounded`
(the code is `_grounded` in
`backend/poseidon/tasks/reporting/skills/monthly_performance/tools/narrate.py`; the saved run
row records `grounded: true`). If the narrative reads as the template, or the proof line does
not say `grounded`, treat it as a fail and capture the items in section 9 plus the backend log:
`docker compose -f infra/docker-compose.yml -f infra/docker-compose.cortex.yml logs backend`.

Stop the stack afterwards (from the repo root, `cd <path-to-clone>`):
`docker compose -f infra/docker-compose.yml -f infra/docker-compose.cortex.yml down`.

## 9. What to capture on failure

1. The pytest output file (`parity.txt` or `cortex-live.txt`).
2. The `LLMResponse.text`. The provider turns failures into `cortex error: <type>` or
   `cortex error: HTTP <status>`, visible in the pytest failure message. A missing account URL
   or token is different: `ProviderCredentialsError` is raised immediately, with a message
   naming the missing variable (`SNOWFLAKE_ACCOUNT_URL` or `SNOWFLAKE_CORTEX_TOKEN`).
3. For an HTTP error, the Snowflake error body, from `curl` against the same endpoint with the
   same token (redact the token before you paste anything):

   ```bash
   curl -sS -i -X POST "$SNOWFLAKE_ACCOUNT_URL/api/v2/cortex/v1/messages" \
     -H "Authorization: Bearer $SNOWFLAKE_CORTEX_TOKEN" \
     -H "X-Snowflake-Authorization-Token-Type: PROGRAMMATIC_ACCESS_TOKEN" \
     -H "anthropic-version: 2023-06-01" \
     -H "content-type: application/json" \
     -d '{"model":"claude-haiku-5-5","max_tokens":64,"messages":[{"role":"user","content":"Say hello."}]}'
   ```

   PowerShell: use `curl.exe` (not the `curl` alias), `$env:SNOWFLAKE_ACCOUNT_URL` and
   `$env:SNOWFLAKE_CORTEX_TOKEN`.
4. The model id, the account region, and the token type used.

The error text is generated by **Snowflake**, not Anthropic. The codes Snowflake documents for
this endpoint: 400 `invalid_request_error` / unknown model, 402 budget exceeded, 403 Not
Authorized, 429 too many requests, 503 inference timed out.

## 10. Replace the recorded fixtures

The offline tests replay recorded Cortex response bodies in
`backend/tests/fixtures/providers/cortex/*.json`. Those were built from the documentation, not
from a real account. Please save **one real `plain_completion` response body** (the curl in
section 9 item 3 with the "Say hello." prompt produces one) **and one real `tool_call` response
body** (a request that includes a `tools` array and a prompt that triggers it) and attach them
to the signoff, token redacted. Response bodies contain no credentials. The repo owner will
refresh the fixtures as a reviewed diff.

## 11. Where to record the result

Fill in `docs/snowflake/CORTEX_BUILD_SIGNOFF.md` and return it with the attachments.

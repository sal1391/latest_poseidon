# Smoke test runbook (either deployed target)

Phase 14 Task 6 (docs/architecture/07-infrastructure.md section 8's owed either-target
checklist). Runs the same way against **either** deployed target -- set `TARGET_DOMAIN` once and
every command below is copy-pasteable as written. EC2 rows are fully concrete today (Phase 14).
Rows that depend on the SPCS deploy existing are marked **(SPCS -- Phase 15, not yet
applicable)** -- this document is written once, now, so Phase 15 only has to add its own
access-mechanism notes, not rewrite the checklist.

## 0. Preconditions -- set these before running anything below

```bash
export TARGET_DOMAIN=<REPLACE: your domain, e.g. poseidon.duckdns.org>
```

Where a step needs a bearer token: log in at `https://$TARGET_DOMAIN` in a browser first (Auth0
PKCE), open devtools' Network tab, trigger any API call (e.g. send a chat message), and copy the
`Authorization: Bearer <token>` header value from that request.

```bash
export TOKEN=<REPLACE: bearer token copied from the browser session above>
```

### Every `docker compose` command below has two preconditions (EC2)

`docker-compose.ec2.yml` interpolates `${POSEIDON_IMAGE:?}`/`${POSEIDON_DOMAIN:?}` and declares
`env_file: /etc/poseidon/backend.env`, and **Compose V2 resolves both before EVERY subcommand** --
`exec`, `logs`, `ps` and `config` included, not just `up`. So each of the two files below must
already exist, or every compose command in this checklist fails with
`required variable POSEIDON_IMAGE is missing a value` (or an `env file ... not found`) before it
runs anything:

1. **`/opt/poseidon/.env`**, holding `POSEIDON_IMAGE=...` and `POSEIDON_DOMAIN=...` -- Compose
   auto-loads a `.env` sitting beside the compose file, no `--env-file` flag needed.
2. **`/etc/poseidon/backend.env`**, the secret-bearing env file.

Both are authored by `deploy-ec2.md` Steps 3 and 4; this checklist runs after a deploy, so both
are normally already in place. Confirm in one command before starting:

```bash
docker compose -f /opt/poseidon/docker-compose.ec2.yml config >/dev/null && echo "compose OK"
```

If you would rather not use the `.env` file, export the same two values in **every** shell that
runs a compose command instead (`export POSEIDON_IMAGE=... POSEIDON_DOMAIN=...`) -- the file is
recommended because it survives a new SSH session and a reboot.

`/opt/poseidon/...` is the one working-directory convention both runbooks use (see
`deploy-ec2.md` Step 3): the absolute `-f` path means every command here works from any
directory, and pins the Compose project name to `poseidon` so `caddy_data` -- and the issued
certificate in it -- is the same volume every time.

### `POSEIDON_IMAGE` as a real shell variable, for the two `docker run` steps

Section 5's PDF render-verify is a plain `docker run`, **not** a compose subcommand, so it does
not see `/opt/poseidon/.env` at all:

```bash
export POSEIDON_IMAGE=<REPLACE: the same ECR reference /opt/poseidon/.env names, e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com/poseidon:a1b2c3d>
```

### A host-side Python, for the JSON/UUID one-liners

A couple of steps below need a small host-side JSON/UUID helper (parsing a `curl` response,
generating a `client_turn_key`) -- pure standard-library one-liners, unrelated to the app's own
code, so unlike this checklist's `docker compose exec backend python ...` calls (which need the
container's `poseidon` package) these run directly on whatever machine you are running this
checklist from. `python3` is not guaranteed to resolve on a typical Windows install (only
`python`/`py` usually do); this resolves once, up front, to whichever name is actually on `PATH`:

```bash
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
```

### The logged-in user's `sub`, for sections 7 and 8

Sections 7/8 count rows in RLS-protected tables, which means they must run as the same identity
that produced them (see those sections for why a bare connection reports 0 on RDS). Capture it
from the session established in section 3 -- run this once section 3's login has happened:

```bash
export USER_SUB=$(curl -s "https://$TARGET_DOMAIN/api/me" \
  -H "Authorization: Bearer $TOKEN" | "$PY" -c "import sys,json; print(json.load(sys.stdin)['sub'])")
echo "$USER_SUB"   # e.g. auth0|68a1... -- must be non-empty
```

---

## 1. Health endpoints

```bash
curl -s -o /dev/null -w "live=%{http_code}\n"  "https://$TARGET_DOMAIN/health/live"
curl -s "https://$TARGET_DOMAIN/health/ready"
```

- [ ] `/health/live` -> `200`.
- [ ] `/health/ready` -> `200`, body `{"status":"ok","components":{"db":"up"}}`.

## 2. The identity checklist line (restated independently of the deploy runbook)

```bash
# EC2: exec into the running container.
docker compose -f /opt/poseidon/docker-compose.ec2.yml exec backend python -c \
  "from poseidon.core.config import get_settings; print(get_settings().identity_mode)"
```

- [ ] Prints `auth0`. **Never `disabled` outside `local`.** A deploy that ever shows anything
  else here is serving every request as the fixed dev user with no real authentication, on a
  publicly reachable box -- stop and fix before continuing this checklist.
- **(SPCS -- Phase 15, not yet applicable.)** The SPCS default is `spcs_ingress`, not `auth0`
  (decision D22) -- the equivalent assertion there is `identity_mode == "spcs_ingress"`, and the
  exec mechanism is `SYSTEM$GET_SERVICE_LOGS`/an in-service shell rather than `docker compose
  exec`.

## 3. Login

- [ ] **Real round-trip, a user with the `Poseidon:Sales` role**: open
  `https://$TARGET_DOMAIN` in a private/incognito window, log in with a test user that has the
  role, land on the chat UI, see the composer.
- [ ] **Role-less user gets 403**: log in with the second test user (no role assigned). Confirm
  the app surfaces a clear "no access" state rather than a raw error page or a silently broken
  composer (`GET /api/me` returns `200` with an empty `roles` list; any `/api/conversations` or
  chat-send call gets `403` with an RFC-7807 body).
- [ ] **Standing risk to check first, not just once**: if the dev trial Auth0 tenant (expires
  ~2026-08-25) is what this deploy points at, confirm today's date is still before that -- an
  expired tenant fails this whole section closed (every login attempt errors at Auth0's own
  side, not this app's).
- **(SPCS -- Phase 15, not yet applicable.)** SPCS's default identity is `spcs_ingress` (the
  platform authenticates the visitor as a Snowflake user at the edge) -- there is no Auth0
  login screen to test there by default; the role-less-403 equivalent is a Snowflake user not on
  `SPCS_SALES_USERS`' allowlist.

## 4. All three flows, on synthetic data

Using the logged-in session from section 3 (the `Poseidon:Sales` user):

- [ ] **Default Q&A**: ask `Top GP customers for Port of Singapore in April 2026` -> a `table`
  part (top-5 customers by GP) + a collapsible proof block + a certified-answer line.
- [ ] **One carry-over pivot**: follow up with `and for May 2026?` -> the period carries/replaces
  to May 2026 without repeating the port or metric (the same carry-over semantics
  `infra/runbooks/local.md`'s own 4-turn gate script exercises).
- [ ] **Existing-customer brief**: click **Existing customer**, type a real seeded customer name
  (e.g. `Northstar Lines`), send -> the six-metric grid, top-ports table, and five phase
  sections stream in, ending in a proof block.
- [ ] **Prospect brief**: click **New customer prospect**, type any company name (does not need
  to be a real customer -- the prospect path never resolves one), send -> four phase sections
  stream in (Operational Profile and Web Research before Context and Strategy, per D10's
  research-first ordering).

## 5. Artifact PDF download

- [ ] From the existing-customer brief above (or any turn that produces one), confirm an
  `artifact` part renders and its link downloads a real PDF (not a 404, not an HTML error page).

### PDF render-verify on the production image

Independent of whether the UI download above worked -- this proves WeasyPrint's native
rendering path on the exact image running in production, not merely that `import weasyprint`
succeeds (which is all Task 5's own rehearsal proved):

```bash
docker run --rm "$POSEIDON_IMAGE" python -m pytest -m pdf
```

- [ ] All `pdf`-marked tests pass, zero skips. (`$POSEIDON_IMAGE` is the same
  `<account>.dkr.ecr.us-east-1.amazonaws.com/poseidon:<tag>` reference `deploy-ec2.md` used to
  deploy.)

## 6. One real streaming chat turn THROUGH Caddy (not direct-to-8000)

This is the one item this checklist exists partly to answer: whether the SSE response reaches the
client token-by-token or arrives buffered. `infra/Caddyfile` is written to make buffering
impossible (`flush_interval -1`, plus an `encode` whitelist that excludes `text/event-stream`),
but "written to be impossible" is not evidence. There is no direct evidence either way before
this runs -- `backend` publishes no port on EC2 (see
`docker-compose.ec2.yml`'s own comment), so **every** request in this whole checklist already
goes through Caddy by construction; this section is the one that specifically watches for
buffering rather than just checking a final status code.

```bash
CONV_ID=$(curl -s -X POST "https://$TARGET_DOMAIN/api/conversations" \
  -H "Authorization: Bearer $TOKEN" | "$PY" -c "import sys,json; print(json.load(sys.stdin)['id'])")
CTK=$("$PY" -c "import uuid; print(uuid.uuid4())")

curl -N -s "https://$TARGET_DOMAIN/api/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"text\":\"Top GP customers for Port of Singapore in April 2026\",\"client_turn_key\":\"$CTK\"}" \
  | while IFS= read -r line; do printf '%s  %s\n' "$(date +%H:%M:%S.%3N)" "$line"; done
```

- [ ] **Expected (progressive rendering):** the printed timestamps spread across roughly a
  second or more -- `accepted`, then `tool` (start/done), then `part` frames (table, proof), then
  one or more `token` frames, then `done`, each landing at a visibly later timestamp than the
  one before it, mirroring what the browser UI shows as text appearing progressively rather than
  all at once.
- [ ] **If it buffers instead** (every line's timestamp is identical, or everything arrives only
  once the turn finishes): the two mitigations this section used to propose are **both already
  shipped** in `infra/Caddyfile` -- `flush_interval -1` on the `reverse_proxy` (never buffer),
  and, since the Phase 14 final-review wave, an explicit `encode gzip { match { ... } }`
  content-type whitelist that omits `text/event-stream` so an SSE response is never handed to the
  compressor at all. So buffering here is NOT the known-and-mitigated case anymore; it is a new
  finding. Confirm the running config is actually the current file first --

  ```bash
  docker compose -f /opt/poseidon/docker-compose.ec2.yml exec caddy cat /etc/caddy/Caddyfile
  ```

  (an old Caddyfile left on the box from a previous deploy is the likeliest explanation; re-scp
  it per `deploy-ec2.md` Step 3 and `docker compose -f /opt/poseidon/docker-compose.ec2.yml up -d
  caddy`). If the running config IS current and it still buffers, record it as a real finding for
  the fix-wave process rather than patching the file live.

## 7. `turn_run` + `llm_calls` + `message_feedback` rows present

**These tables are RLS-protected, so the query must carry an identity.** On RDS the app's own
database user is `NOSUPERUSER`/`NOBYPASSRLS` and every one of these tables is `FORCE ROW LEVEL
SECURITY` with a `user_sub` policy -- a plain `engine.connect()` with no `app.user_sub` set
matches no rows at all and reports `0` for everything, which reads exactly like a broken deploy
and is not one. Go through the app's own seam (`poseidon.core.db.rls_transaction`, the same
function every store in the app uses) with the `$USER_SUB` captured in section 0:

```bash
docker compose -f /opt/poseidon/docker-compose.ec2.yml \
  exec -e SMOKE_USER_SUB="$USER_SUB" backend python -c "
import os
import sqlalchemy as sa
from poseidon.core.config import get_settings
from poseidon.core.db import build_engine, rls_transaction
settings = get_settings()
engine = build_engine(settings.database_url)
with rls_transaction(engine, os.environ['SMOKE_USER_SUB'], app_role=settings.database_app_role) as c:
    for table in ('turn_run', 'llm_calls', 'message_feedback'):
        n = c.execute(sa.text('SELECT count(*) FROM ' + table)).scalar()
        print(table, n)
"
```

> `$USER_SUB` is a variable of the shell on the box, not of the container, so it is passed in
> explicitly with `exec -e` -- and `-e` must come **before** the service name, which is why the
> command is wrapped the way it is.

- [ ] `turn_run` > 0 (one row per turn sent in sections 4/6 above).
- [ ] `llm_calls` > 0 (one row per model invocation those turns made).
- [ ] Leave a thumbs-up or thumbs-down on one assistant message in the UI, then re-run the query
  above -- `message_feedback` goes from 0 to >= 1.
- [ ] **If all three read `0`:** check `$USER_SUB` is the sub of the user who actually sent those
  turns (section 3's logged-in user) before concluding anything is wrong with the deploy -- a
  correct-but-different identity legitimately sees none of another user's rows. That is RLS
  working, and this checklist counting rows through it is the point.

## 8. Memory distillation fires

Temporarily lower `MEMORY_IDLE_MINUTES` via a compose override (never edit the tracked
`docker-compose.ec2.yml` for this):

```bash
cat > /tmp/memory-idle-override.yml <<'YAML'
services:
  worker:
    environment:
      MEMORY_IDLE_MINUTES: "1"
YAML
docker compose -f /opt/poseidon/docker-compose.ec2.yml -f /tmp/memory-idle-override.yml up -d worker
```

Have at least one chat turn in a conversation (section 4 already provided one), then wait past
the lowered threshold (a couple of minutes is plenty), then check:

Same RLS discipline as section 7 -- `turn_run` and `user_memory` are both `user_sub`-scoped, so
this reads through `rls_transaction` with the same `$USER_SUB`, never a bare connection (a bare
one prints two empty lists on RDS and looks exactly like distillation having failed):

```bash
docker compose -f /opt/poseidon/docker-compose.ec2.yml \
  exec -e SMOKE_USER_SUB="$USER_SUB" backend python -c "
import os
import sqlalchemy as sa
from poseidon.core.config import get_settings
from poseidon.core.db import build_engine, rls_transaction
settings = get_settings()
engine = build_engine(settings.database_url)
with rls_transaction(engine, os.environ['SMOKE_USER_SUB'], app_role=settings.database_app_role) as c:
    print('memory_update turn_run rows:')
    for r in c.execute(sa.text(\"SELECT id, status, created_at FROM turn_run WHERE kind='memory_update' ORDER BY created_at DESC LIMIT 3\")):
        print(' ', r)
    print('user_memory versions:')
    for r in c.execute(sa.text('SELECT user_sub, version, created_at FROM user_memory ORDER BY created_at DESC LIMIT 3')):
        print(' ', r)
"
```

- [ ] A `turn_run` row with `kind='memory_update'` appears, `status='ok'`.
- [ ] A new `user_memory` row (a higher `version` than existed before) appears for that user.

**Restore the setting** immediately after -- do not leave the idle threshold lowered:

```bash
rm /tmp/memory-idle-override.yml
docker compose -f /opt/poseidon/docker-compose.ec2.yml up -d worker
```

- [ ] Confirm restored: `docker compose -f /opt/poseidon/docker-compose.ec2.yml exec worker python -c
  "from poseidon.core.config import get_settings; print(get_settings().memory_idle_minutes)"`
  prints `30` again (the packaged default; the compose file itself never overrides it).

## 9. `/docs` 404s outside `local`

```bash
for path in docs redoc openapi.json; do
  curl -s -o /dev/null -w "%{http_code} /$path\n" "https://$TARGET_DOMAIN/$path"
done
```

- [ ] All three print `404`. (`DEPLOY_MODE=ec2` closes all three per Task 2's docs-surface
  gating -- only `local` serves them.)

## 10. Rate-limit sanity: burst -> 429 with `Retry-After`

`IDENTITY_MODE=auth0` resolves the chat-send limiter to 30/minute by default
(`effective_rate_limit_chat_per_minute`, `core/config.py`). Burst past it.

**The burst must be CONCURRENT, and this step costs real money.** Two things follow from that,
both deliberate:

- *Concurrent, not sequential.* Each accepted send runs a real chat turn to completion before the
  response closes -- seconds, not milliseconds. A `for` loop that waits for each response spreads
  32 requests over a minute or more, the 30/minute window slides underneath it, and the run never
  429s at all: the check silently passes by never testing anything. Firing all 32 at once is the
  only shape that actually crosses the limit.
- *Real Bedrock spend.* The ~30 requests that ARE accepted each run a live model turn on the
  deployed stack. It is small (short "ping" turns on the cheapest routed path), but it is not
  zero and it is not a mock. Run this once, deliberately, not in a retry loop.

```bash
CONV_ID=$(curl -s -X POST "https://$TARGET_DOMAIN/api/conversations" \
  -H "Authorization: Bearer $TOKEN" | "$PY" -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 32 client_turn_keys up front -- generating uuids inside the loop would
# serialize the very thing that has to be parallel.
CTKS=$("$PY" -c "import uuid; print('\n'.join(str(uuid.uuid4()) for _ in range(32)))")

# -P 32: all 32 in flight at once. Prints one status code per line, in
# completion order (order does not matter here -- the COUNT of each does).
echo "$CTKS" | xargs -P 32 -I {} \
  curl -s -o /dev/null -w "%{http_code}\n" -X POST \
    "https://$TARGET_DOMAIN/api/conversations/$CONV_ID/messages" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"text\":\"ping\",\"client_turn_key\":\"{}\"}" \
  | sort | uniq -c
```

If `xargs -P` is unavailable, background jobs do the same thing:

```bash
for CTK in $CTKS; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST \
    "https://$TARGET_DOMAIN/api/conversations/$CONV_ID/messages" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"text\":\"ping\",\"client_turn_key\":\"$CTK\"}" &
done
wait
```

- [ ] The tally shows both codes: roughly 30 `200`s (accepted turns) and the remainder `429`.
  The split need not be exactly 30/2 -- what matters is that `429` appears at all and `200`s
  stop appearing beyond about 30.
- [ ] Inspect one `429` directly for the header: `curl -s -D - -o /dev/null -X POST ... | grep -i
  retry-after` -> a `Retry-After` header naming a positive number of seconds is present.
- [ ] Confirm the 401/403 path is untouched: a request with no `Authorization` header (or an
  expired one) still gets `401`/`403`, never a `429` that would mask the real problem
  (`require_sales` always runs before the rate limiter, per `api/auth.py`'s own docstring).

## 11. Free-disk and cert-issuance checks (EC2 habitat)

```bash
ssh -i <REPLACE: your key>.pem ec2-user@<REPLACE: the instance's Elastic IP> df -h /
docker compose -f /opt/poseidon/docker-compose.ec2.yml logs caddy | grep -i "certificate obtained\|obtaining certificate\|certificate.*error" || true
```

- [ ] `df -h /` shows headroom well above the 2G swapfile plus the image (roughly 1GB free is a
  reasonable floor to flag on a 20G root volume before it becomes urgent).
- [ ] Caddy's logs show a successful certificate obtained for `$TARGET_DOMAIN`, no repeating
  issuance errors (which would mean either DNS is not pointed at this instance yet, or an ACME
  rate limit was hit from redeploying too many times without the `caddy_data` volume intact).
- **(SPCS -- Phase 15, not yet applicable.)** SPCS has no EC2 disk or Caddy-issued certificate
  at all -- the equivalent checks there are block-volume free space (doc 07 section 4's own
  "checked in the post-deploy smoke run" note) and the platform's own `ingress_url` TLS, which
  SPCS manages, not this app.

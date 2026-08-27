# Deploy runbook: EC2 (first-deployed target)

Phase 14 Task 6 (docs/architecture/07-infrastructure.md section 5, decision D8 revised
2026-08-05: EC2 deploys first, SPCS remains the corporate primary target, deployed after).
Written so someone who has never seen this repo can execute it top to bottom. Every command
below is copy-pasteable; every value that is genuinely account- or environment-specific is
marked `<REPLACE: why>` rather than left as an unexplained placeholder.

## Prerequisites

- AWS CLI v2, authenticated (`aws sts get-caller-identity` succeeds), region `us-east-1`.
- Docker (with the `docker compose` plugin) on the machine that builds the image -- **not** on
  the EC2 box itself. A t3-class instance should not be running `npm run build`; the image is
  built locally and pushed to ECR (Step 2).
- An Auth0 tenant with an SPA application and an API (see `docs/superpowers/plans/
  2026-08-03-aws-auth0-setup.task.md`). **Standing risk:** Carlos's plan is to reuse the dev
  trial tenant set up there, which expires around 2026-08-25 -- if this deploy happens after
  that date, redo Track B of that runbook against a fresh tenant (or a paid one) before Step 2's
  build below (the `VITE_AUTH0_*` values are baked into the image, so a tenant change after that
  point means rebuilding), since `IDENTITY_MODE=auth0` with an expired tenant fails closed (401
  on every request).
- A domain name pointed (or about to be pointed) at the instance's Elastic IP. The default
  recommendation is a free dynamic-DNS subdomain (DuckDNS-style); the final choice is made live
  at the walkthrough. Whatever it is, it is a **parameter** (`POSEIDON_DOMAIN`) to Caddy and to
  `05-ec2.sh` -- nothing in this repo hardcodes a hostname.
- An SSH key pair already created in the target region (`KEY_NAME` for `05-ec2.sh`).

## Step 1 -- Account prep: run the provisioning scripts, in order

All six live in `infra/aws/`, are idempotent (check-before-create; safe to re-run), and accept
`--dry-run` (native EC2 `--dry-run` where the CLI supports it, a print-only check-mode where it
does not -- each script's own header says which). None hardcodes a credential or an
account-specific value; every account-specific input is an environment variable. Each script
also sets `MSYS_NO_PATHCONV=1` itself (a no-op outside Git Bash on Windows), so a leading-slash
argument -- `05-ec2.sh`'s SSM parameter lookup is the one that actually needs it today -- is not
silently rewritten into a Windows path before `aws.exe` ever sees it; nothing extra to set by
hand here.

```bash
cd infra/aws

ADMIN_CIDR=<REPLACE: your admin IP in CIDR form, e.g. 203.0.113.4/32> ./01-security-groups.sh
DB_MASTER_PASSWORD=<REPLACE: a strong password> ./02-rds.sh
S3_BUCKET_NAME=<REPLACE: or leave unset to resolve poseidon-artifacts-ACCOUNTID> ./03-s3.sh
S3_BUCKET_NAME=<REPLACE: same bucket as above> ./04-iam.sh
KEY_NAME=<REPLACE: your EC2 key pair name> ./05-ec2.sh
./06-budget.sh
```

Notes:

- `02-rds.sh` returns immediately after `create-db-instance` is accepted; the instance takes
  several minutes to become `available`. Poll with the command the script prints, and do not
  proceed to Step 4 until it reports `available` with an `Endpoint.Address`.
- `05-ec2.sh` prints the instance's public IP once its Elastic IP is associated. Point
  `POSEIDON_DOMAIN`'s DNS A record at that IP now -- Caddy's automatic-HTTPS step (Step 5) needs
  the name to already resolve, or certificate issuance fails.
- `06-budget.sh` is verification only: it prints the existing AWS Budget alert (created
  2026-08-03) rather than creating a duplicate one.

## Step 2 -- Build the image locally and push to ECR

One image serves both API and SPA (`infra/Dockerfile`); the `VITE_AUTH0_*` values are baked in
at **build** time (Vite inlines them into the bundle), so an Auth0 tenant change means a rebuild,
not a restart -- doc 07 section 5's disclosed deviation from the one-image-configured-at-runtime
ideal.

```bash
cd <REPLACE: path where this repo is checked out on the machine building the image>

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
IMAGE_TAG=$(git rev-parse --short HEAD)
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/poseidon"

# Repo creation is check-before-create too.
aws ecr describe-repositories --region "$REGION" --repository-names poseidon >/dev/null 2>&1 || \
  aws ecr create-repository --region "$REGION" --repository-name poseidon

aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# DOCKER_BUILDKIT=1 is NOT decorative. infra/Dockerfile.dockerignore is a
# per-Dockerfile ignore file, and ONLY BuildKit reads those; there is no root
# .dockerignore to fall back on, so a legacy (non-BuildKit) build silently ships
# the whole repo -- .git, node_modules, and any .env sitting on this machine --
# into the build context and, through the Dockerfile's COPY steps, potentially into
# the image. Docker 23+ defaults to BuildKit, but this pins it rather than trusting
# the daemon's default.
DOCKER_BUILDKIT=1 docker build -f infra/Dockerfile -t "poseidon:${IMAGE_TAG}" \
  --build-arg VITE_AUTH0_DOMAIN=<REPLACE: your-tenant.us.auth0.com> \
  --build-arg VITE_AUTH0_CLIENT_ID=<REPLACE: the SPA client id> \
  --build-arg VITE_AUTH0_AUDIENCE=https://poseidon/api \
  .

docker tag "poseidon:${IMAGE_TAG}" "${ECR_REPO}:${IMAGE_TAG}"
docker push "${ECR_REPO}:${IMAGE_TAG}"
```

**Tag convention: `poseidon:<git-sha>`, never a moving label like `:latest`.** Rollback (see
below) is redeploying the *previous* sha's tag -- that only means anything if the tag you are
rolling back to is still pinned to the code it was built from.

## Step 3 -- Put the two infra files on the box, at `/opt/poseidon`

Nothing in Steps 1-2 has put a single file from this repo onto the instance. Two are needed
there: `infra/docker-compose.ec2.yml` and `infra/Caddyfile`. They must sit in the **same
directory**, because the compose file bind-mounts the Caddyfile as `./Caddyfile`, resolved
relative to the compose file's own location.

From the machine that has this repo checked out (the same one that built the image in Step 2):

```bash
ssh -i <REPLACE: your .pem key file>.pem ec2-user@<REPLACE: the instance's Elastic IP, from 05-ec2.sh's output> \
  'sudo mkdir -p /opt/poseidon && sudo chown ec2-user:ec2-user /opt/poseidon'

scp -i <REPLACE: your .pem key file>.pem \
  infra/docker-compose.ec2.yml infra/Caddyfile \
  ec2-user@<REPLACE: the instance's Elastic IP>:/opt/poseidon/
```

(`scp` from the repo root, so those two relative paths resolve. A `git clone` on the box would
work equally well; `scp` of exactly two files is chosen because it needs no git, no credentials,
and no repo on a t3-class instance -- but pick one and stay with it, since a half-cloned repo
and a half-scp'd pair of files is how the two drift apart.)

**`/opt/poseidon` is THE convention, and both runbooks use it.** Every `docker compose` command
in this runbook and in `infra/runbooks/smoke.md` is written with the absolute form:

```
docker compose -f /opt/poseidon/docker-compose.ec2.yml ...
```

Written that way it works from any directory at all -- no `cd` step to forget, and no "which
directory was I supposed to be in" ambiguity between the two documents. It also pins the Compose
**project name** to `poseidon` (Compose derives it from the compose file's parent directory, not
from your shell's working directory), and that matters: the project name is what the `caddy_data`
volume is namespaced under, so an inconsistent invocation would hand Caddy an empty volume, no
certificate, and a fresh round of Let's Encrypt issuance -- the exact rate-limit trap this
compose file's own comment warns about.

### `/opt/poseidon/.env` -- the two variables the compose file interpolates

`docker-compose.ec2.yml` interpolates `${POSEIDON_IMAGE:?}` and `${POSEIDON_DOMAIN:?}`. **Compose
V2 interpolates the whole file before EVERY subcommand** -- not just `up`, but `pull`, `ps`,
`logs`, `exec`, and `config` too. Any one of them run without both variables set fails
immediately with

```
error while interpolating services.worker.image: required variable POSEIDON_IMAGE is missing a value
```

before it does anything else. Write both into a `.env` file beside the compose file, and every
later command in both runbooks is copy-pasteable as written:

```bash
# ON THE BOX -- ssh in first:
#   ssh -i <REPLACE: your .pem key file>.pem ec2-user@<REPLACE: the instance's Elastic IP>
# (Step 4 continues in this same session.)
cat > /opt/poseidon/.env <<'ENVFILE'
POSEIDON_IMAGE=<REPLACE: ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/poseidon:<REPLACE: IMAGE_TAG from Step 2>
POSEIDON_DOMAIN=<REPLACE: your domain, e.g. poseidon.duckdns.org>
ENVFILE
```

Compose loads a `.env` sitting in the compose file's own directory automatically -- no
`--env-file` flag, anywhere, ever. This survives reboots, new SSH sessions and a different
operator, which an `export` in one shell does not.

- **This file holds no secret.** An image reference and a hostname are both public by nature.
  Everything secret lives in `/etc/poseidon/backend.env` (Step 4), a different file with
  different ownership and mode, for exactly that reason.
- **It never reaches the application.** It is read by the `docker compose` CLI on the host, to
  fill in `${...}` in the compose file. The containers themselves get `POSEIDON_ENV_FILE=""`
  and read no dotenv at all.
- **Alternative, if you prefer no file:** export both in *every* shell that runs a compose
  command --
  `export POSEIDON_IMAGE=... POSEIDON_DOMAIN=...` -- remembering that a new SSH session, a
  reboot, or a `sudo` that does not preserve the environment starts over with neither set.
- **`docker run` does not read it.** The PDF render-verify below (and `smoke.md`'s own copy) is
  a plain `docker run`, not a compose subcommand, so it needs `POSEIDON_IMAGE` exported in that
  shell regardless of this file.

## Step 4 -- Author `/etc/poseidon/backend.env` on the box **before any `docker compose` command**

**This step comes strictly before Step 5, `docker compose config` included.** Every subcommand
`docker compose -f /opt/poseidon/docker-compose.ec2.yml ...` can run -- `up`, `config`, `logs`,
all of them -- reads `env_file: /etc/poseidon/backend.env` and fails immediately if that file
does not exist yet, even for a command that does not start anything. Together with Step 3's
`.env`, these are the **two** preconditions every compose command in both runbooks has: the
`.env` next to the compose file (interpolation) and this file (the `env_file:` reference).
There is no working order that defers either past Step 5.

SSH to the instance (`ssh -i <REPLACE: your .pem key file>.pem ec2-user@<REPLACE: the instance's Elastic IP, from 05-ec2.sh's output>`), then, as root:

```bash
sudo mkdir -p /etc/poseidon
sudo tee /etc/poseidon/backend.env >/dev/null <<'ENVFILE'
DATABASE_URL=postgresql+psycopg://poseidon:<REPLACE: DB_MASTER_PASSWORD from 02-rds.sh>@<REPLACE: RDS endpoint from 02-rds.sh>:5432/poseidon
AUTH0_DOMAIN=<REPLACE: your-tenant.us.auth0.com>
AUTH0_AUDIENCE=https://poseidon/api
AUTH0_CLIENT_ID=<REPLACE: the SPA client id -- same value baked into the image in Step 2>
S3_BUCKET=<REPLACE: the bucket 03-s3.sh created>
# PERPLEXITY_API_KEY=<REPLACE: optional -- omit this line entirely to run without live web research>
ENVFILE
sudo chown root:root /etc/poseidon/backend.env
sudo chmod 600 /etc/poseidon/backend.env
```

### The full contract: every key, and which ones are secret

Everything below either comes baked into `docker-compose.ec2.yml` itself (non-secret, readable
in git) or from this one file (environment-specific, and never committed):

| Key | Secret? | Where it lives | Value on EC2 |
|---|---|---|---|
| `DEPLOY_MODE` | no | compose (baked in) | `ec2` |
| `DATA_BACKEND` | no | compose (baked in) | `synthetic` |
| `IDENTITY_MODE` | no | compose (baked in) | `auth0` -- **see the identity checklist below** |
| `LLM_PROFILE` | no | compose (baked in) | `bedrock` |
| `LLM_MODE` | no | compose (baked in) | `live` |
| `CHAT_MODE` | no | compose (baked in, `backend` only) | `live` |
| `STATIC_DIR` | no | compose (baked in, `backend` only) | `/app/static` |
| `TOOL_TRANSPORT_PERPLEXITY` | no | compose (baked in) | `direct` |
| `POSEIDON_ENV_FILE` | no | compose (baked in) | `""` (read no dotenv) |
| `DATABASE_URL` | **yes** -- carries the RDS master password | `/etc/poseidon/backend.env` | see above |
| `AUTH0_DOMAIN` | no | `/etc/poseidon/backend.env` | your tenant |
| `AUTH0_AUDIENCE` | no | `/etc/poseidon/backend.env` | `https://poseidon/api` |
| `AUTH0_CLIENT_ID` | no | `/etc/poseidon/backend.env` | an SPA client id is public by design |
| `S3_BUCKET` | no | `/etc/poseidon/backend.env` | a bucket name is not a secret |
| `PERPLEXITY_API_KEY` | **yes**, optional | `/etc/poseidon/backend.env` | omit the line to run without live web research |

**Bedrock and S3 need no credential in this file at all.** `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` /
`S3_SECRET_KEY` are deliberately absent from both compose and the env file -- boto3's default
credential chain resolves both Bedrock and S3 through the EC2 instance profile `04-iam.sh`
attached (`poseidon-ec2-profile`), so the box never holds a long-lived AWS key to leak or rotate.

## Step 5 -- First deploy

Back on the box, with the image pushed in Step 2, the two infra files and `/opt/poseidon/.env`
in place from Step 3, and `/etc/poseidon/backend.env` authored in Step 4. Confirm both
preconditions in one command first -- `config` interpolates the file *and* reads the env file,
so it fails loudly if either is missing and prints the resolved configuration if neither is:

```bash
docker compose -f /opt/poseidon/docker-compose.ec2.yml config >/dev/null && \
  echo "compose config resolves: both .env and /etc/poseidon/backend.env are in place"
```

Then:

```bash
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <REPLACE: ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

docker compose -f /opt/poseidon/docker-compose.ec2.yml pull
docker compose -f /opt/poseidon/docker-compose.ec2.yml up -d
```

The `docker login` line is not optional: ECR pulls are authenticated. It needs no credential
file on the box -- `04-iam.sh`'s instance profile carries `ecr:GetAuthorizationToken` plus
read-only pull on the `poseidon` repository, so the AWS CLI resolves it through the instance
role and no static key is involved. The token expires after 12 hours; re-run the login line
before a later deploy if `pull` reports `no basic auth credentials`.

### Expected first-boot note: the worker container may exit and restart once or twice

**This is expected behavior, not a failure.** `backend`'s start command runs `alembic upgrade
head` before `uvicorn` ever starts (migrations 0009/0010 create the `poseidon_worker` role and
the `poseidon_app` membership grant this deploy depends on). `worker` starts concurrently
(`depends_on: backend` only waits for the container to start, not for migrations to finish,
since `backend` declares no healthcheck) and calls
`assert_boot_privileges(..., require_worker_role=True)` before its first cycle. Until migration
0009 has actually run, that probe refuses with exactly this message and the container exits:

```
RuntimeError: the memory worker requires the poseidon_worker role, which does not exist in
pg_roles on this database. ... Fix: run `python -m alembic upgrade head` against this database
(migration 0009 creates poseidon_worker, grants it on memory_outbox, and grants membership to
the migration's own DSN user).
```

`restart: unless-stopped` (every service in this compose file) brings it back up automatically;
once `backend`'s migration step has finished, the next restart succeeds and the worker settles
into its normal idle-poll cycle. Watch it settle with:

```bash
docker compose -f /opt/poseidon/docker-compose.ec2.yml logs -f worker
```

One or two restarts in the first minute or so is normal. A worker still cycling after `backend`
has been healthy and serving traffic for several minutes is not -- check `docker compose logs
backend` for a migration failure first.

## Step 6 -- Verify

```bash
curl -s https://<REPLACE: your domain>/health/ready
curl -s https://<REPLACE: your domain>/health/live
docker compose -f /opt/poseidon/docker-compose.ec2.yml logs backend --tail 50
docker compose -f /opt/poseidon/docker-compose.ec2.yml logs caddy --tail 20
```

`/health/ready` should report `{"status":"ok","components":{"db":"up"}}`. Then run the full
`infra/runbooks/smoke.md` checklist against `https://<REPLACE: your domain>` -- it is the actual
post-deploy verification; the two curls above are only "did anything start at all".

### PDF render-verify on the production image

WeasyPrint has so far only been import-verified (`import weasyprint` succeeds in the image, per
Task 5's rehearsal) -- this is the first time a real PDF render is asked to run against this
exact production image, native libraries and all:

```bash
# `docker run` is NOT a compose subcommand: it does not read /opt/poseidon/.env,
# so POSEIDON_IMAGE has to be a real shell variable here.
export POSEIDON_IMAGE=<REPLACE: the same ECR reference written into /opt/poseidon/.env in Step 3>
docker run --rm "${POSEIDON_IMAGE}" python -m pytest -m pdf
```

This needs no database or network access (the `pdf`-marked tests exercise `build_brief_pdf`'s
WeasyPrint rendering path directly); it does need the `pdf` and `minio`-marked tests' own
fixtures to tolerate a missing MinIO -- if a test in this run genuinely requires `S3_ENDPOINT_URL`
it will skip, not fail, per `infra/runbooks/local.md`'s own note on this marker. All PDF-only
tests should pass with zero skips.

### The identity checklist line

**Deployed `IDENTITY_MODE` MUST read `auth0` -- never `disabled` outside `local`.** Confirm it
directly:

```bash
docker compose -f /opt/poseidon/docker-compose.ec2.yml exec backend python -c \
  "from poseidon.core.config import get_settings; print(get_settings().identity_mode)"
```

Expect `auth0`. If this ever prints `disabled` on a real deploy, stop: that means every request
is running as the fixed dev user with no real authentication at all, on a publicly reachable
box. (`smoke.md` re-asserts this same line independently, since a deploy runbook checking its own
work is not the same as an outside verification pass finding it too.)

## Rollback

Migrations are now a **hard precondition of API start** on RDS: `assert_boot_privileges` refuses
to serve if the `poseidon_app`/`poseidon_worker` roles are missing or unassumable, and the
`backend` start chain runs `alembic upgrade head` before `uvicorn` ever starts. Rollback is
therefore the paired operation doc 07 section 4's migration-rollback contract already states for
SPCS, restated here for EC2:

```bash
# 1. Downgrade the schema to the revision the PREVIOUS image expects.
docker compose -f /opt/poseidon/docker-compose.ec2.yml exec backend python -m alembic downgrade <REPLACE: the alembic revision id the previous image's code expects -- see backend/migrations/versions/>

# 2. Redeploy the PREVIOUS image tag -- never the schema alone, never the image alone.
#    Rollback is an EDIT to the one line in /opt/poseidon/.env, then `up -d`: the image
#    reference lives in exactly one place, so "which sha is deployed" stays answerable by
#    reading that file. Do NOT prefix the command with POSEIDON_IMAGE=... instead -- that
#    rolls back this one invocation while the file still names the new tag, and the next
#    `up -d` anyone runs silently rolls forward again.
sed -i 's|^POSEIDON_IMAGE=.*|POSEIDON_IMAGE=<REPLACE: ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/poseidon:<REPLACE: PREVIOUS_TAG>|' \
  /opt/poseidon/.env
docker compose -f /opt/poseidon/docker-compose.ec2.yml up -d
```

**Downgrading past 0009/0010 is deliberately self-defeating, by design.** Migration 0009 creates
the `poseidon_worker` role and its `memory_outbox` grant; migration 0010 grants `poseidon_app`
membership to the migrations' own DSN user. A rollback that downgrades past either one puts the
database back into the exact state `assert_boot_privileges` exists to refuse:

- Past **0009**: the worker's boot probe fails every time it starts (the exact message quoted in
  Step 5's first-boot note) -- the worker will not boot until 0009 is re-applied.
- Past **0010**, on a non-superuser DSN (real RDS, never this project's local superuser DSN): the
  **API itself** refuses to boot -- `DATABASE_APP_ROLE=poseidon_app` names a role this
  connection can no longer assume, and `assert_boot_privileges` treats that as a fatal
  misconfiguration rather than letting every request 500 individually.

This is not a bug in the rollback path -- it is the boot probe doing exactly its job. Never
downgrade past 0009/0010 as part of an EC2 rollback unless the image being rolled back to
genuinely predates both migrations (in which case that image could never have booted against
this database's *current* schema anyway, and the correct rollback target is further back still).

## RDS restore path

Point-in-time restore to a new instance, then swap the DSN in `/etc/poseidon/backend.env`.
Written to meet an RTO of next business day (the owner-decided number, doc 07 section 4);
rehearsable on request, and rehearsing it spins temporary RDS spend (a second `db.t3.micro`
instance exists for the duration of the rehearsal).

```bash
aws rds restore-db-instance-to-point-in-time \
  --region us-east-1 \
  --source-db-instance-identifier poseidon-db \
  --target-db-instance-identifier poseidon-db-restore \
  --restore-time <REPLACE: an ISO-8601 timestamp within the backup window, or use --use-latest-restorable-time instead of --restore-time> \
  --db-subnet-group-name poseidon-db-subnet-group \
  --vpc-security-group-ids <REPLACE: poseidon-rds-sg's id, from 01-security-groups.sh's output>
```

Once the restored instance reports `available` (poll the same way Step 1's `02-rds.sh` note
describes):

1. Update `/etc/poseidon/backend.env`'s `DATABASE_URL` to the restored instance's endpoint.
2. `docker compose -f /opt/poseidon/docker-compose.ec2.yml up -d` (re-reads the env file; `backend` reruns
   `alembic upgrade head`, a no-op if the restored snapshot is already at `head`).
3. Run `infra/runbooks/smoke.md` in full against the live URL before declaring the restore done.
4. Once confirmed, decide whether to keep `poseidon-db-restore` as the new primary (rename it,
   update DNS/env permanently) or discard it after confirming the original `poseidon-db` is fine
   -- this script only ever creates the *new* instance, it never touches or deletes the source.

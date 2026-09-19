# Phase 16 — Chat Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a chat turn from the Next.js app and watch it stream through AI SDK with every part kind intact, while Python keeps running the turn behind an authenticated internal contract and Drizzle takes over the application schema.

**Architecture:** Bridge, not port (spec §3). Python's `send_message` body is extracted into one shared `stream_turn` that a new internal route exposes under a shared-secret header; a Next.js route handler calls it and translates each SSE envelope frame into an AI SDK UI-message chunk through one pure translator that a shared contract corpus pins on both sides. Drizzle becomes the migration authority through a hand-corrected schema plus three migrations (roles, generated baseline, grants and forced RLS), proven by Python's own pg suite against a database Alembic never touched. Four hardening carry-overs land first.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy Core (unchanged). Next.js 16.3.4, React 19.2.8, Drizzle ORM 0.45 + drizzle-kit 0.31.10, **AI SDK 6.x line** (`ai@6.0.286`, `@ai-sdk/react@3.0.289`, per Carlos 2026-09-19; NOT the 7.x `latest`), `react-markdown@10.1.0`, `uuid@11` (v7 ids), vitest 5, jsdom + @testing-library/react for renderer tests.

**Spec:** `docs/superpowers/specs/2026-09-19-phase-16-chat-parity-design.md` — the plan argues from it; executors read both. The migration design `docs/superpowers/specs/2026-09-07-nextjs-migration-design.md` is the parent.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python interpreter:** always `backend/.venv/Scripts/python.exe`, never a bare `python`. Run tests from `backend/`.
- **Offline Python suite command (the gate for every Python task):**
  `env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' > ../.superpowers/sdd/2026-09-19-phase-16-chat-parity/pytest-<task>.log 2>&1; tail -5 ../.superpowers/sdd/2026-09-19-phase-16-chat-parity/pytest-<task>.log`. Baseline at branch start: **1693 passed, 13 skipped, 116 deselected.** Never pipe pytest; redirect and tail the file. Never run `ruff format`; `ruff check .` must stay clean.
- **Postgres-backed suite:** `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg` with `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon`. Bring the db up with `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml up -d db` (host 5432 is held by another project). Last recorded figure 334 passed / 1 skipped, stale.
- **Web suite:** `cd web && npm test` (offline: 60 passed, 9 skipped at branch start); with `DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon` the db-backed files run too (69 passed). `npx tsc --noEmit` must stay clean. Web tests use `vi.stubGlobal("fetch", ...)`, `vi.stubEnv(...)`, and `afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); })`; db-backed files gate on `const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;`.
- **Frozen surfaces (CLAUDE.md):** the SSE envelope and its frame kinds, the ten part kinds, migrations 0001–0010 (never edited, never deleted; from Task 8 on they are history, not the boot path), `rls_transaction` / `set_config(..., true)` / `SET LOCAL ROLE`, provider-prefixed subs, the certified ontology. Nothing in this plan changes a frame's shape.
- **Sanctioned modifications** (the complete file set; a task touching a file not listed here stops and reports):
  - Python: `backend/poseidon/core/config.py`, `backend/poseidon/api/internal.py`, `backend/poseidon/api/internal_auth.py` (new), `backend/poseidon/api/app.py`, `backend/poseidon/api/auth.py`, `backend/poseidon/api/live_chat.py`, `backend/poseidon/api/dev_runner.py`, `backend/poseidon/core/data/factory.py` (new), `backend/poseidon/core/data/synthetic_schema.py` (new), `backend/poseidon/core/db.py` (three hint strings only), `backend/poseidon/scripts/seed_synthetic.py`, `backend/tests/test_internal_dispatch.py`, `backend/tests/test_internal_turns.py` (new), `backend/tests/test_data_factory.py` (new), `backend/tests/test_synthetic_schema.py` (new), `backend/tests/test_boot_privileges.py` (assertion strings only), `backend/tests/test_dev_runner.py` (if it pins the 501 text), `backend/tests/contracts/__init__.py`, `backend/tests/contracts/scenarios.py`, `backend/tests/contracts/test_turn_contracts.py` (all new), `backend/tests/fixtures/synthetic_schema_columns.json` (new).
  - Shared: `contracts/turns/*.json` and `contracts/turns/README.md` (new tree).
  - Web: `web/package.json`, `web/package-lock.json`, `web/drizzle.config.ts`, `web/drizzle/**` (new), `web/vitest.config.ts` (new), `web/src/instrumentation.ts` (new), `web/src/proxy.ts`, `web/src/lib/identity.ts` (export only), `web/src/lib/request-identity.ts`, `web/src/lib/skills.ts`, `web/src/lib/turns.ts` (new), `web/src/lib/turn-stream.ts` (new), `web/src/lib/message-parts.ts` (new), `web/src/lib/opener.ts` (new), `web/src/lib/conversations.ts`, `web/src/db/client.ts`, `web/src/db/boot.ts` (new), `web/src/db/schema.ts`, `web/src/ui/message-parts/*` (new), `web/src/app/page.tsx`, `web/src/app/c/[id]/page.tsx`, `web/src/app/c/[id]/Chat.tsx` (new), `web/src/app/api/chat/[cid]/route.ts` (new), `web/README.md`, and their `*.test.ts(x)` siblings.
  - Infra and docs: `infra/docker-compose.yml`, `CLAUDE.md`, `docs/architecture/00-overview.md`, `docs/superpowers/specs/2026-09-07-nextjs-migration-design.md`.
- **Git:** explicit-path `git add` only. Never stage the five untracked August docs under `docs/superpowers/plans/`. Never stage `.superpowers/`. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. No push, no PR.
- **TDD is the evidence.** Every task shows the new test failing first, says why that failure is the right one, then passing. The implementer runs the suites; the reviewer never re-runs them.
- **Copy rules:** every user-visible string in this plan is verbatim. Problem bodies are RFC 7807 `{type, title, detail, status}` built by `poseidon.core.skills.result.problem` on the Python side and `proxy.ts`'s `problem()` on the web side. Python's Sales denial detail is exactly `caller lacks required role 'Poseidon:Sales'` (single quotes, from `{_REQUIRED_ROLE!r}`); the web copy matches it byte for byte.
- **AI SDK facts verified 2026-09-19 (context7, ai@6.0.0):** `text-start` → `text-delta` → `text-end` with a matching `id` is mandatory (the consumer throws otherwise); `start-step`/`finish-step` are optional; `start` and `finish` carry `messageId?`/`messageMetadata?`; `data-<name>` chunks carry `{id?, data, transient?}` and a repeated `id` updates in place; an `error` chunk is non-terminal; `createUIMessageStream({execute})` returns `ReadableStream<UIMessageChunk>`; `readUIMessageStream({stream})` yields `UIMessage` snapshots; `DefaultChatTransport({api, prepareSendMessagesRequest})` returns the whole request body from `prepareSendMessagesRequest`.
- **Next.js facts verified 2026-09-19:** a route handler under `app/api/...` wins over the `afterFiles` rewrite that `next.config.ts`'s `rewrites()` array form produces, so `/api/chat/*` needs no rewrite change (Task 14 proves it live). `forbidden()` is still behind `experimental.authInterrupts`, so the proxy answers 403 itself (Task 5). `params` is a Promise. `instrumentation.ts` `register()` runs once per server instance under `NEXT_RUNTIME === "nodejs"`.
- **drizzle-kit facts:** `pgRole("x").existing()` keeps a role out of generated SQL; `pgTable(...).enableRLS()`; policies attach in the array form `(t) => [pgPolicy(name, {for, to, using, withCheck})]`; `drizzle-kit generate --custom --name=<n>` writes an empty SQL file to fill by hand; statements in a migration file are separated by `--> statement-breakpoint`. The migrations folder layout under drizzle-kit 0.31 is `web/drizzle/<NNNN>_<name>.sql` plus `web/drizzle/meta/_journal.json` and `meta/<NNNN>_snapshot.json`; Task 7 verifies this on disk and reports if it differs.

---

## Task order and dependencies

| # | Task | Depends on |
|---|---|---|
| 1 | Python internal-route auth + trace-id adoption | — |
| 2 | Web internal client: token, trace id, fail-fast | 1 (header names) |
| 3 | Data-client factory | — |
| 4 | Web boot privilege probe | — |
| 5 | Sales gate in web | — |
| 6 | Corrected Drizzle schema | — |
| 7 | Drizzle migrations (roles, baseline, grants) + tripwire | 6 |
| 8 | Boot order: synthetic schema via seed, compose migrate service, Alembic retired, pg gate | 7 |
| 9 | Python shared `stream_turn` + internal turns route | 1, 3 |
| 10 | Contract corpus (scenarios, golden test, opener) | 9 |
| 11 | Web translator + adapter + contract test | 10 |
| 12 | Web renderer registry | — |
| 13 | Conversation creation in web + opener parity | 5, 10 |
| 14 | Chat route handler + chat page | 2, 11, 12, 13 |
| 15 | Docs, decision log, spec edits, README | all |

Tasks 1, 3, 4, 5, 6, 12 are independent and may be dispatched in parallel (separate files, no shared state). Task 8 is the only task that needs Docker; run it when the 5434 db is up.

---

### Task 1: Python internal-route auth and trace-id adoption

**Files:**
- Modify: `backend/poseidon/core/config.py` (after `database_app_role`, line 176 and its validator at 216-230)
- Create: `backend/poseidon/api/internal_auth.py`
- Modify: `backend/poseidon/api/internal.py:56` (router construction)
- Modify: `backend/poseidon/api/app.py:284-312` (`_install_trace_middleware`)
- Test: `backend/tests/test_internal_dispatch.py`

**Interfaces:**
- Consumes: `poseidon.core.identity.AuthError(status, title, detail)` (rendered as a problem body by `app.add_exception_handler(AuthError, auth.auth_error_response)`, app.py:71); `poseidon.core.obs.new_trace_id()`.
- Produces: `Settings.internal_token: str | None`; `poseidon.api.internal_auth.INTERNAL_TOKEN_HEADER = "X-Poseidon-Internal-Token"` and `require_internal_token(request) -> None`; the internal router now carries `include_in_schema=False` and `dependencies=[Depends(require_internal_token)]`; the trace middleware adopts a well-formed inbound `X-Trace-Id` on `/internal/` paths. Task 2 and Task 9 rely on the header name and the 401/503 bodies below.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_internal_dispatch.py`. First change the existing helpers so every existing test keeps passing once the dependency exists:

```python
# in _settings(**overrides): add this keyword to the Settings(...) call
        internal_token="test-internal-token",

# replace the client fixture
_TOKEN_HEADERS = {"X-Poseidon-Internal-Token": "test-internal-token"}


@pytest.fixture()
def client():
    return TestClient(_app(), headers=_TOKEN_HEADERS)
```

Replace `test_the_route_is_mounted_in_every_habitat` (it asserted presence via `app.openapi()["paths"]`, which `include_in_schema=False` removes on purpose):

```python
@pytest.mark.parametrize("deploy_mode", ["local", "spcs", "ec2"])
def test_the_route_is_mounted_in_every_habitat_but_hidden_from_openapi(deploy_mode):
    app = _app(deploy_mode=deploy_mode)
    assert DISPATCH_PATH_TEMPLATE not in app.openapi()["paths"], deploy_mode
    resp = TestClient(app, headers=_TOKEN_HEADERS).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="no.such_skill"),
        json={"args": {}, "identity": _SALES_IDENTITY},
    )
    # 404 with the route's OWN detail (naming the skill) proves the route ran;
    # an unmounted path would be FastAPI's bare {"detail": "Not Found"}.
    assert resp.status_code == 404, deploy_mode
    assert "no.such_skill" in resp.json()["detail"], deploy_mode
```

New tests:

```python
def _dispatch_body():
    return {"args": {}, "identity": _SALES_IDENTITY}


def test_missing_internal_token_is_401_problem():
    resp = TestClient(_app()).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="no.such_skill"), json=_dispatch_body()
    )
    assert resp.status_code == 401
    assert resp.json() == {
        "type": "about:blank",
        "title": "internal token invalid",
        "detail": "missing or wrong X-Poseidon-Internal-Token header",
        "status": 401,
    }


def test_wrong_internal_token_is_401():
    resp = TestClient(_app(), headers={"X-Poseidon-Internal-Token": "nope"}).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="no.such_skill"), json=_dispatch_body()
    )
    assert resp.status_code == 401


def test_unset_internal_token_refuses_every_caller_with_503():
    resp = TestClient(_app(internal_token=None), headers=_TOKEN_HEADERS).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="no.such_skill"), json=_dispatch_body()
    )
    assert resp.status_code == 503
    assert resp.json()["title"] == "internal contract not configured"
    assert "POSEIDON_INTERNAL_TOKEN" in resp.json()["detail"]


def test_blank_internal_token_setting_means_unset():
    from poseidon.core.config import Settings

    assert _settings(internal_token="   ").internal_token is None


def test_internal_routes_adopt_a_well_formed_inbound_trace_id(client):
    trace = "0" * 31 + "a"
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="no.such_skill"),
        json=_dispatch_body(),
        headers={"X-Trace-Id": trace},
    )
    assert resp.headers["X-Trace-Id"] == trace


def test_internal_routes_ignore_a_malformed_inbound_trace_id(client):
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="no.such_skill"),
        json=_dispatch_body(),
        headers={"X-Trace-Id": "not-hex; drop table"},
    )
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Trace-Id"])


def test_public_routes_never_adopt_an_inbound_trace_id():
    resp = TestClient(_app()).get("/health/live", headers={"X-Trace-Id": "0" * 32})
    assert resp.headers["X-Trace-Id"] != "0" * 32
```

(Add `import re` at the top. If `/health/live` is not the liveness path, use whatever `backend/poseidon/api/health.py` mounts; the assertion is about any non-internal route.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_internal_dispatch.py -q > ../.superpowers/sdd/2026-09-19-phase-16-chat-parity/pytest-t1-red.log 2>&1; tail -15 ../.superpowers/sdd/2026-09-19-phase-16-chat-parity/pytest-t1-red.log`

Expected: `_settings(internal_token=...)` raises a pydantic validation error (extra="ignore" means the kwarg is silently dropped, so instead the 401/503 tests fail with `200`/`404` where 401/503 is expected, and `test_the_route_is_mounted_in_every_habitat_but_hidden_from_openapi` fails on the `not in app.openapi()` line). Either shape is the right failure: the setting, the dependency and the schema hiding do not exist yet.

- [ ] **Step 3: Add the setting**

In `backend/poseidon/core/config.py`, directly after the `database_app_role` field (line 176):

```python
    # Phase 16 Task 1 (spec section 5.2): the shared secret every route under
    # api/internal.py requires in the X-Poseidon-Internal-Token header. No
    # default on purpose -- unset means the internal routes answer 503 to
    # every caller rather than letting one through. Compose injects a dev
    # value into `backend`; the Next.js app reads the SAME variable name.
    internal_token: str | None = None
```

And after `blank_database_app_role_means_none` (line 230):

```python
    @field_validator("internal_token", mode="before")
    @classmethod
    def blank_internal_token_means_none(cls, v: object) -> object:
        """``POSEIDON_INTERNAL_TOKEN=`` in a compose file means unset, the
        same way ``DATABASE_APP_ROLE=`` does (validator above)."""
        if isinstance(v, str) and not v.strip():
            return None
        return v
```

- [ ] **Step 4: Write the dependency**

Create `backend/poseidon/api/internal_auth.py`:

```python
"""The shared-secret gate on every ``/internal/v1`` route (spec section 5.2).

Phase 15 shipped ``api/internal.py`` trusting its body's ``{sub, roles}``
completely, protected only by "nothing outside the service network can
reach it". Ruling R25 recorded that as a must-fix before any deploy. This
module is the fix: the web tier presents ``POSEIDON_INTERNAL_TOKEN`` in a
header, compared in constant time.

Three outcomes, all RFC 7807 problem bodies via the ``AuthError`` handler
``app.py`` already installs:

* setting unset          -> 503 "internal contract not configured"
* header missing / wrong -> 401 "internal token invalid"
* match                  -> the route runs

503 rather than a pass-through when unset is the whole point: a
misconfigured service refuses, it never silently trusts.
"""

import secrets

from fastapi import Request

from poseidon.core.identity import AuthError

INTERNAL_TOKEN_HEADER = "X-Poseidon-Internal-Token"


def require_internal_token(request: Request) -> None:
    expected = request.app.state.settings.internal_token
    if expected is None:
        raise AuthError(
            503,
            "internal contract not configured",
            "POSEIDON_INTERNAL_TOKEN is unset on this service, so every "
            "/internal/v1 route refuses every caller until it is set",
        )
    presented = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    ):
        raise AuthError(
            401,
            "internal token invalid",
            f"missing or wrong {INTERNAL_TOKEN_HEADER} header",
        )
```

- [ ] **Step 5: Attach it to the router and hide the router from OpenAPI**

In `backend/poseidon/api/internal.py`, replace line 56:

```python
from fastapi import APIRouter, Depends, HTTPException, Request

from poseidon.api.internal_auth import require_internal_token
...
router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Phase 16 Task 1: never published. The OpenAPI document is a public
    # surface; an internal contract listed there invites callers it must
    # refuse. test_internal_dispatch.py proves the routes are still mounted
    # by calling them.
    include_in_schema=False,
    dependencies=[Depends(require_internal_token)],
)
```

Update the module docstring's "The body is a claim, not a credential" paragraph: keep it, and add one sentence: "Since Phase 16 the caller must also present ``POSEIDON_INTERNAL_TOKEN`` (``internal_auth.py``); the network boundary is defence in depth, no longer the only gate."

- [ ] **Step 6: Adopt the inbound trace id on internal paths**

In `backend/poseidon/api/app.py`, inside `_install_trace_middleware` (lines 284-312), replace `trace_id = new_trace_id()` in `trace_middleware` with:

```python
        # Phase 16 Task 1 (spec section 5.3): the web tier mints one trace id
        # per request and forwards it; adopting it makes one turn one trace
        # across both runtimes. Only on /internal/ paths (the caller is our
        # own service) and only when well-formed (new_trace_id()'s own
        # 32-hex shape) -- a browser can never write our log correlation key.
        inbound = request.headers.get("X-Trace-Id", "")
        if request.url.path.startswith("/internal/") and _TRACE_ID_SHAPE.fullmatch(inbound):
            trace_id = inbound
        else:
            trace_id = new_trace_id()
```

with, at module level near the other constants, `_TRACE_ID_SHAPE = re.compile(r"[0-9a-f]{32}")` (add `import re`).

- [ ] **Step 7: Run the tests to verify they pass**

Run the same command as Step 2 with `-t1-green.log`. Expected: all tests in the file pass, including the ten pre-existing ones (they now send the header via the fixture).

- [ ] **Step 8: Run the offline suite and ruff**

Run the offline suite command from Global Constraints with `-t1` in the log name, then `.venv/Scripts/python.exe -m ruff check .`. Expected: baseline count + 8 new tests passed (report the exact numbers), ruff clean. If any other test built the internal route without a token and now gets 503, that test's settings helper gains `internal_token="test-internal-token"` and its client the header; report each such edit.

- [ ] **Step 9: Commit**

```bash
git add backend/poseidon/core/config.py backend/poseidon/api/internal_auth.py backend/poseidon/api/internal.py backend/poseidon/api/app.py backend/tests/test_internal_dispatch.py
git commit -m "feat(api): shared-secret gate and trace-id adoption on the internal contract (R25)"
```

---

### Task 2: Web internal client sends the token and a trace id, fails fast when unset

**Files:**
- Modify: `web/src/lib/skills.ts`
- Test: `web/src/lib/skills.test.ts`

**Interfaces:**
- Consumes: Task 1's header names (`X-Poseidon-Internal-Token`, `X-Trace-Id`; HTTP headers are case-insensitive, the web side writes them lower-case).
- Produces, exported from `web/src/lib/skills.ts` for Task 14's `turns.ts`: `internalHeaders(traceId?: string): Record<string, string>` (content-type + token + trace id), `newTraceId(): string` (32 lower-case hex), `analyticsOrigin(): string` (already exists, now exported), `INTERNAL_TOKEN_HEADER = "x-poseidon-internal-token"`, `TRACE_ID_HEADER = "x-trace-id"`.

- [ ] **Step 1: Write the failing tests**

In `web/src/lib/skills.test.ts`, add to the file's top-level `beforeEach` (create one if absent): `vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "test-internal-token");`. Then add:

```ts
import { internalHeaders, newTraceId } from "./skills";

describe("internal contract headers", () => {
  it("sends the token and a fresh 32-hex trace id on every dispatch", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, parts: [], proof: [], artifacts: [], error: null }));
    vi.stubGlobal("fetch", fetchMock);
    await dispatchSkill("data_qa.metric_query", {}, { sub: "dev|local", roles: ["Poseidon:Sales"] });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["x-poseidon-internal-token"]).toBe("test-internal-token");
    expect(init.headers["x-trace-id"]).toMatch(/^[0-9a-f]{32}$/);
  });

  it("throws before calling fetch when POSEIDON_INTERNAL_TOKEN is unset", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      dispatchSkill("data_qa.metric_query", {}, { sub: "dev|local", roles: [] }),
    ).rejects.toThrow(/POSEIDON_INTERNAL_TOKEN is unset/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("internalHeaders honours a caller-supplied trace id", () => {
    const h = internalHeaders("a".repeat(32));
    expect(h["x-trace-id"]).toBe("a".repeat(32));
    expect(h["content-type"]).toBe("application/json");
  });

  it("newTraceId is 32 lower-case hex characters", () => {
    expect(newTraceId()).toMatch(/^[0-9a-f]{32}$/);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npx vitest run src/lib/skills.test.ts`
Expected: FAIL — `internalHeaders` / `newTraceId` are not exported; the header assertions fail because `init.headers` has only `content-type`. Right failure: the client does not know about the token yet.

- [ ] **Step 3: Implement**

In `web/src/lib/skills.ts`, above `dispatchSkill`:

```ts
export const INTERNAL_TOKEN_HEADER = "x-poseidon-internal-token";
export const TRACE_ID_HEADER = "x-trace-id";

/**
 * The shared secret Python's `require_internal_token` compares against
 * (`backend/poseidon/api/internal_auth.py`). Same variable name in both
 * runtimes on purpose (ruling R26: one name per value). Read per call, like
 * `analyticsOrigin()`. Unset or blank throws BEFORE any request is made, so a
 * misconfigured web tier fails on the first turn with the variable's name in
 * the message, not with a 503 it cannot explain.
 */
function internalToken(): string {
  const raw = process.env.POSEIDON_INTERNAL_TOKEN;
  if (raw === undefined || raw.trim() === "") {
    throw new Error(
      "POSEIDON_INTERNAL_TOKEN is unset: the internal contract cannot be called " +
        "(set it to the same value the analytics service was started with)",
    );
  }
  return raw;
}

/** Same shape as Python's `new_trace_id()` (`core/obs.py`): 32 lower-case hex, no dashes. */
export function newTraceId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

export function internalHeaders(traceId: string = newTraceId()): Record<string, string> {
  return {
    "content-type": "application/json",
    [INTERNAL_TOKEN_HEADER]: internalToken(),
    [TRACE_ID_HEADER]: traceId,
  };
}
```

Export `analyticsOrigin` (change `function analyticsOrigin` to `export function analyticsOrigin`). In `dispatchSkill`, replace `headers: { "content-type": "application/json" },` with `headers: internalHeaders(),` and move that call above the `try` so the throw happens before the timer is armed:

```ts
  const headers = internalHeaders(); // throws when the token is unset -- before any timer or fetch
  const limit = timeoutMs();
  ...
      headers,
```

- [ ] **Step 4: Run to verify pass**

Run: `cd web && npx vitest run src/lib/skills.test.ts`. Expected: PASS, every pre-existing test included.

- [ ] **Step 5: Full web suite and type check**

Run: `cd web && npm test && npx tsc --noEmit`. Expected: 64 passed (60 + 4), 9 skipped; tsc clean.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/skills.ts web/src/lib/skills.test.ts
git commit -m "feat(web): internal client sends POSEIDON_INTERNAL_TOKEN and a trace id, fails fast when unset"
```

---

### Task 3: One data-client factory (R24 follow-up)

**Files:**
- Create: `backend/poseidon/core/data/factory.py`
- Modify: `backend/poseidon/api/internal.py` (the `SkillContext(data=...)` line), `backend/poseidon/api/dev_runner.py:56-90` (`_build_ctx`), `backend/poseidon/api/app.py:565`
- Test: `backend/tests/test_data_factory.py` (new); `backend/tests/test_internal_dispatch.py` (one test); `backend/tests/test_dev_runner.py` only if it pins the 501 detail text (keep the text identical so it should not)

**Interfaces:**
- Produces: `poseidon.core.data.factory.build_data_client(settings) -> DataClient` and `DataBackendUnavailable(RuntimeError)` with attribute `.backend: str`. Task 9's internal turns route does not construct a data client (it reads `app.state.data_client`), so this task closes the hazard for dispatch, dev runner and app wiring.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_data_factory.py`:

```python
"""Phase 16 Task 3: every data client is built through one factory, so a
backend with no adapter raises loudly at construction instead of querying an
empty `synthetic` schema and answering zero rows with confidence (Phase 15
final review, R24 follow-up)."""

import pytest

from poseidon.core.config import Settings
from poseidon.core.data.factory import DataBackendUnavailable, build_data_client
from poseidon.core.data.synthetic_client import SyntheticDataClient

_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, database_url=_DSN, s3_bucket="poseidon-artifacts")
    base.update(overrides)
    return Settings(**base)


def test_synthetic_backend_builds_the_synthetic_client():
    client = build_data_client(_settings(data_backend="synthetic"))
    assert isinstance(client, SyntheticDataClient)


def test_snowflake_backend_raises_before_any_client_exists():
    with pytest.raises(DataBackendUnavailable) as excinfo:
        build_data_client(_settings(data_backend="snowflake"))
    assert excinfo.value.backend == "snowflake"
    assert "snowflake" in str(excinfo.value)
```

Add to `backend/tests/test_internal_dispatch.py`:

```python
def test_dispatch_on_a_backend_without_an_adapter_is_a_501_problem():
    resp = TestClient(_app(data_backend="snowflake"), headers=_TOKEN_HEADERS).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="probe.identity"),
        json={"args": {}, "identity": _SALES_IDENTITY},
    )
    assert resp.status_code == 501
    assert resp.json()["title"] == "backend not implemented"
```

(If `_app()` cannot register the probe skill without a registry helper, use `_registry_recording([])` the way `test_the_body_identity_reaches_the_skill_not_the_middleware_user` does, and assert the recorder stayed empty — the 501 must fire before dispatch.)

- [ ] **Step 2: Run to verify failure**

Run both files. Expected: `ModuleNotFoundError: poseidon.core.data.factory`, and the dispatch test gets 200 (the route builds `SyntheticDataClient` unconditionally today). Right failure.

- [ ] **Step 3: Implement the factory**

Create `backend/poseidon/core/data/factory.py`:

```python
"""The one place a DataClient is constructed from settings (spec section 9).

`api/dev_runner.py` guarded `data_backend != "synthetic"` with a structured
501; `api/internal.py` and `api/app.py` did not, and once `alembic upgrade
head` (now `drizzle-kit migrate` plus the seed's schema step) has created the
EMPTY `synthetic` schema, a Snowflake-configured deploy dispatching a data
skill answers zero rows confidently. Raising here makes every construction
site inherit the guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from poseidon.core.data.synthetic_client import SyntheticDataClient

if TYPE_CHECKING:
    from poseidon.core.config import Settings
    from poseidon.core.data.client import DataClient


class DataBackendUnavailable(RuntimeError):
    def __init__(self, backend: str) -> None:
        self.backend = backend
        super().__init__(
            f"data_backend={backend!r} has no adapter yet - the Snowflake client "
            "arrives Phase 19; only data_backend=synthetic is supported today"
        )


def build_data_client(settings: Settings) -> DataClient:
    if settings.data_backend == "synthetic":
        return SyntheticDataClient(settings.database_url)
    raise DataBackendUnavailable(settings.data_backend)
```

- [ ] **Step 4: Route the three construction sites through it**

`backend/poseidon/api/internal.py` — replace `data=SyntheticDataClient(settings.database_url),` with a guarded build placed before the `SkillContext(...)`. Return the problem body directly rather than raising `HTTPException(detail=<dict>)`, which would wrap it as `{"detail": {...}}` while the test reads `resp.json()["title"]` at the top level:

```python
    try:
        data = build_data_client(settings)
    except DataBackendUnavailable as exc:
        return JSONResponse(
            status_code=501, content=problem(501, "backend not implemented", str(exc))
        )
```

then `data=data,` in the `SkillContext`. Imports: `from fastapi.responses import JSONResponse`, `from poseidon.core.data.factory import DataBackendUnavailable, build_data_client`, `from poseidon.core.skills.result import problem`; drop the `SyntheticDataClient` import; change the route's return annotation to `-> Any`.

`backend/poseidon/api/dev_runner.py` `_build_ctx` — replace the `if settings.data_backend != "synthetic": return SkillResult(...)` block with:

```python
    try:
        data = build_data_client(settings)
    except DataBackendUnavailable as exc:
        return SkillResult(
            ok=False,
            error=problem(
                501,
                "backend not implemented",
                f"data_backend={settings.data_backend!r} has no adapter yet - "
                "the Snowflake client arrives Phase 15; the dev runner only "
                "supports data_backend=synthetic today",
            ),
        )
```

(the detail text is kept byte-identical to today's so `test_dev_runner.py` keeps passing; `exc` is unused on purpose — the message is pinned) and `data=data,` in the `SkillContext`.

`backend/poseidon/api/app.py:565` — replace `app.state.data_client = SyntheticDataClient(settings.database_url)` with:

```python
    try:
        app.state.data_client = build_data_client(settings)
    except DataBackendUnavailable as exc:
        # Boot still succeeds: live_chat.py's own snowflake guard answers every
        # turn with a structured 501 frame before app.state.data_client is
        # ever read (see _snowflake_guard_response). None makes an accidental
        # read fail loudly instead of querying an empty schema.
        app.state.data_client = None
        get_logger(__name__).warning("data_client_unavailable", extra={"backend": exc.backend})
```

Check how `app.py` obtains a logger (it imports from `poseidon.core.obs`); use that module's convention. Drop the now-unused `SyntheticDataClient` import if nothing else in app.py uses it (`ruff` will say).

- [ ] **Step 5: Run to verify pass, then the offline suite and ruff**

Both new tests pass; offline suite = previous count + 3; ruff clean. `test_live_chat_sse.py`'s snowflake tests are pg-marked; the reasoning in Step 4 keeps them valid, and Task 8 runs the pg suite.

- [ ] **Step 6: Commit**

```bash
git add backend/poseidon/core/data/factory.py backend/poseidon/api/internal.py backend/poseidon/api/dev_runner.py backend/poseidon/api/app.py backend/tests/test_data_factory.py backend/tests/test_internal_dispatch.py
git commit -m "feat(data): one build_data_client factory; non-synthetic backends fail at construction (R24)"
```

---

### Task 4: Web boot privilege probe (R21)

**Files:**
- Modify: `web/src/db/client.ts` (export `appRole`, add `assertBootPrivileges`)
- Create: `web/src/db/boot.ts`, `web/src/instrumentation.ts`
- Test: `web/src/db/boot.test.ts` (new)

**Interfaces:**
- Consumes: `postgres` (postgres.js) client API: `` client`...` `` tagged query, `client.begin(async (tx) => ...)` which rolls back when the callback throws, `client.end()`.
- Produces: `assertBootPrivileges(client: ReturnType<typeof postgres>, role: string | null): Promise<void>`; `appRole(): string | null` exported; `bootCheck(): Promise<void>` in `boot.ts`; `register()` in `instrumentation.ts`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/db/boot.test.ts`:

```ts
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import postgres from "postgres";
import { assertBootPrivileges } from "./client";

describe("assertBootPrivileges (offline)", () => {
  it("logs boot_privileges_unchecked and resolves when the database is unreachable", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = postgres("postgresql://nobody:nope@127.0.0.1:1/void", { max: 1, connect_timeout: 1 });
    await expect(assertBootPrivileges(client, "poseidon_app")).resolves.toBeUndefined();
    expect(warn.mock.calls.some(([line]) => String(line).includes("boot_privileges_unchecked"))).toBe(true);
    await client.end();
    warn.mockRestore();
  });
});

// Requires the compose `db` service (its `poseidon` user is a superuser, which
// is exactly the configuration the probe exists to catch). Skipped when unset.
const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("assertBootPrivileges (live)", () => {
  let client: ReturnType<typeof postgres>;
  beforeAll(() => {
    client = postgres(process.env.DRIZZLE_DATABASE_URL!, { max: 1 });
  });
  afterAll(async () => {
    await client?.end();
  });

  it("refuses a superuser DSN with no app role", async () => {
    await expect(assertBootPrivileges(client, null)).rejects.toThrow(/rolsuper or rolbypassrls/);
  });

  it("accepts the app role by rehearsing SET LOCAL ROLE in a rolled-back transaction", async () => {
    await expect(assertBootPrivileges(client, "poseidon_app")).resolves.toBeUndefined();
    const [row] = await client`SELECT current_user AS who`;
    expect(row.who).toBe("poseidon"); // the rehearsal left no role switch behind
  });

  it("names a role that does not exist", async () => {
    await expect(assertBootPrivileges(client, "no_such_role")).rejects.toThrow(/no_such_role/);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npx vitest run src/db/boot.test.ts` (offline) and once more with `DRIZZLE_DATABASE_URL` set if the 5434 db is up. Expected: `assertBootPrivileges is not a function` (not exported). Right failure.

- [ ] **Step 3: Implement the probe**

In `web/src/db/client.ts`: change `function appRole()` to `export function appRole()`. Append:

```ts
/**
 * Port of Python's `assert_boot_privileges` (`backend/poseidon/core/db.py`,
 * `_run_checks` / `_assert_app_role`), ruling R21. Python refuses to boot when
 * the DSN is a superuser or BYPASSRLS role and no app role is set, because
 * every RLS policy is then silently inert for the whole process. Until Phase
 * 16 this client had no such check, so `DATABASE_APP_ROLE=""` against
 * compose's superuser DSN ran with RLS off and nothing said so.
 *
 * An UNREACHABLE database is not a verdict: `/ready`-style checks own that.
 * Log and continue, exactly as Python's `boot_privileges_unchecked` does.
 */
export async function assertBootPrivileges(
  client: ReturnType<typeof postgres>,
  role: string | null,
): Promise<void> {
  let row: { rolsuper: boolean; rolbypassrls: boolean } | undefined;
  try {
    [row] = await client<{ rolsuper: boolean; rolbypassrls: boolean }[]>`
      SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user`;
  } catch (err) {
    console.warn(
      JSON.stringify({ event: "boot_privileges_unchecked", reason: (err as Error).message }),
    );
    return;
  }
  if (role === null) {
    if (row?.rolsuper || row?.rolbypassrls) {
      throw new Error(
        "DATABASE_APP_ROLE is unset, but this DRIZZLE_DATABASE_URL authenticates as a role " +
          "with rolsuper or rolbypassrls: Postgres bypasses row-level security " +
          "unconditionally for such a role, so every RLS policy in this database is " +
          "silently inert for this process and every user's rows are readable by " +
          "every user. Fix: set DATABASE_APP_ROLE=poseidon_app (Drizzle migration " +
          "0000_roles creates it), or point DRIZZLE_DATABASE_URL at a role with neither attribute.",
      );
    }
    return;
  }
  // `role` has already passed APP_ROLE_PATTERN in appRole(); this is the only
  // other interpolation site and it reuses that guarantee.
  if (!APP_ROLE_PATTERN.test(role)) {
    throw new Error(`refusing to rehearse an invalid role identifier ${JSON.stringify(role)}`);
  }
  const ROLLBACK = Symbol("rollback");
  try {
    await client.begin(async (tx) => {
      await tx.unsafe(`SET LOCAL ROLE "${role}"`);
      throw ROLLBACK; // rehearsal only: never commit a role switch at boot
    });
  } catch (err) {
    if (err === ROLLBACK) return;
    throw new Error(
      `SET LOCAL ROLE "${role}" failed for this DRIZZLE_DATABASE_URL (${(err as Error).message}): ` +
        `DATABASE_APP_ROLE=${JSON.stringify(role)} names a role that does not exist or that this ` +
        "DSN's user may not assume, so every request's transaction would fail at its second " +
        "statement. Fix: run `npx drizzle-kit migrate` in web/ (migration 0000_roles creates " +
        `the role, 0002_grants_force_rls grants membership to the migrating user), or GRANT ${role} ` +
        "TO <this DSN's user> directly.",
    );
  }
}
```

Create `web/src/db/boot.ts`:

```ts
import postgres from "postgres";
import { appRole, assertBootPrivileges } from "./client";

/** One short-lived connection, closed before the app serves anything. */
export async function bootCheck(): Promise<void> {
  const url =
    process.env.DRIZZLE_DATABASE_URL ?? "postgresql://poseidon:poseidon@localhost:5432/poseidon";
  const client = postgres(url, { max: 1, connect_timeout: 5 });
  try {
    await assertBootPrivileges(client, appRole());
  } finally {
    await client.end();
  }
}
```

Create `web/src/instrumentation.ts`:

```ts
/**
 * Next.js boot hook: `register()` runs once per server instance. Guarded to
 * the Node runtime because the probe opens a socket. A thrown error here
 * stops the server from starting, which is the point (spec section 9).
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { bootCheck } = await import("./db/boot");
  await bootCheck();
}
```

- [ ] **Step 4: Run to verify pass**

Offline: `cd web && npx vitest run src/db/boot.test.ts` → 1 passed, 3 skipped. Live (5434 db up): `DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon npx vitest run src/db/boot.test.ts` → 4 passed. Note: the live tests need `poseidon_app` to exist; on an Alembic-migrated database it does (migration 0004).

- [ ] **Step 5: Prove the boot hook fires**

Run `cd web && DATABASE_APP_ROLE="" DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon npm run dev` (PowerShell: `$env:DATABASE_APP_ROLE=""; $env:DRIZZLE_DATABASE_URL="..."; npm run dev`), request `http://localhost:3000/` once, and capture the terminal: the server must log the `rolsuper or rolbypassrls` error and refuse the request. Then unset `DATABASE_APP_ROLE` and confirm the page serves. Paste both outputs in the report. If `register()` does not run under `next dev` (Global Constraints flags this as inferred, not quoted), report it; the fallback is calling `bootCheck()` from the root layout once, guarded by a module-level promise.

- [ ] **Step 6: Full web suite, tsc, commit**

```bash
git add web/src/db/client.ts web/src/db/boot.ts web/src/db/boot.test.ts web/src/instrumentation.ts
git commit -m "feat(web): boot privilege probe mirrors Python's assert_boot_privileges (R21)"
```

---

### Task 5: Sales gate in the web tier

**Files:**
- Modify: `web/src/lib/identity.ts` (export `SALES_ROLE`), `web/src/proxy.ts`, `web/src/lib/request-identity.ts`, `web/src/app/page.tsx`, `web/src/app/c/[id]/page.tsx`
- Test: `web/src/proxy.test.ts`, `web/src/lib/request-identity.test.ts`

**Interfaces:**
- Produces: `requireSales(h): { sub: string; roles: string[] }` in `request-identity.ts` (Task 13, 14 use it); the proxy answers 403 for any resolved identity lacking `Poseidon:Sales`. **Spec refinement (§7.5):** the 403 is issued by the proxy, not by a page, because `forbidden()` is experimental in Next 16.3.4; pages and the route handler still call `requireSales` as defence in depth, and it throws (a configuration fault, like `requireSub`) rather than rendering.

- [ ] **Step 1: Write the failing tests**

`web/src/proxy.test.ts` — follow the file's existing pattern for building a `NextRequest` and reading the response. Add:

```ts
describe("Sales gate", () => {
  it("answers 403 with Python's problem body on /api/* when the identity lacks Poseidon:Sales", async () => {
    vi.stubEnv("IDENTITY_MODE", "spcs_ingress");
    vi.stubEnv("DEPLOY_MODE", "spcs");
    vi.stubEnv("SPCS_SALES_USERS", "alice");
    const res = proxy(makeRequest("/api/conversations", { "sf-context-current-user": "bob" }));
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({
      type: "about:blank",
      title: "insufficient role",
      detail: "caller lacks required role 'Poseidon:Sales'",
      status: 403,
    });
  });

  it("answers a 403 HTML page on a page route", async () => {
    vi.stubEnv("IDENTITY_MODE", "spcs_ingress");
    vi.stubEnv("DEPLOY_MODE", "spcs");
    vi.stubEnv("SPCS_SALES_USERS", "alice");
    const res = proxy(makeRequest("/", { "sf-context-current-user": "bob" }));
    expect(res.status).toBe(403);
    expect(res.headers.get("content-type")).toContain("text/html");
    expect(await res.text()).toContain("403");
  });

  it("lets a Sales user through and forwards the roles header", () => {
    vi.stubEnv("IDENTITY_MODE", "spcs_ingress");
    vi.stubEnv("DEPLOY_MODE", "spcs");
    vi.stubEnv("SPCS_SALES_USERS", "alice");
    const res = proxy(makeRequest("/", { "sf-context-current-user": "alice" }));
    expect(res.status).toBe(200);
    expect(forwardedHeader(res, "x-poseidon-roles")).toBe("Poseidon:Sales");
  });
});
```

(`makeRequest` and `forwardedHeader` are whatever helpers the existing tests already use to construct a `NextRequest` and read `NextResponse.next({request:{headers}})`'s forwarded headers; reuse their names. Disabled mode grants Sales to `dev|local`, so the local click path is unaffected.)

`web/src/lib/request-identity.test.ts`:

```ts
import { requireSales } from "./request-identity";

describe("requireSales", () => {
  it("returns sub and roles when the Sales role is present", () => {
    const h = new Headers({ "x-poseidon-sub": "dev|alice", "x-poseidon-roles": "Poseidon:Sales,Other" });
    expect(requireSales(h)).toEqual({ sub: "dev|alice", roles: ["Poseidon:Sales", "Other"] });
  });

  it("throws a configuration fault when the role is missing (the proxy should have answered 403)", () => {
    const h = new Headers({ "x-poseidon-sub": "dev|alice", "x-poseidon-roles": "" });
    expect(() => requireSales(h)).toThrow(/lacks Poseidon:Sales/);
  });

  it("still requires the sub", () => {
    expect(() => requireSales(new Headers({ "x-poseidon-roles": "Poseidon:Sales" }))).toThrow(
      "x-poseidon-sub missing: the proxy did not run for this request",
    );
  });
});
```

- [ ] **Step 2: Run to verify failure**

`cd web && npx vitest run src/proxy.test.ts src/lib/request-identity.test.ts`. Expected: proxy returns 200 for bob (no gate yet); `requireSales` is not exported. Right failure.

- [ ] **Step 3: Implement**

`web/src/lib/identity.ts`: change `const SALES_ROLE = "Poseidon:Sales";` to `export const SALES_ROLE = "Poseidon:Sales";`.

`web/src/proxy.ts`, after identity resolves and before the forwarded headers:

```ts
  // Phase 16 Task 5 (P16-4): the role half of Python's `require_sales`
  // (`backend/poseidon/api/auth.py`), with its exact detail string. Issued
  // here rather than by a page because Next 16.3's `forbidden()` is still
  // behind experimental.authInterrupts. /api/* gets the problem body a
  // client expects; a page navigation gets a small HTML page.
  if (!identity.roles.includes(SALES_ROLE)) {
    const detail = `caller lacks required role '${SALES_ROLE}'`;
    if (request.nextUrl.pathname.startsWith("/api/")) {
      return NextResponse.json(problem(403, "insufficient role", detail), { status: 403 });
    }
    return new NextResponse(FORBIDDEN_PAGE, {
      status: 403,
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }
```

with, at module level:

```ts
const FORBIDDEN_PAGE =
  "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>403</title></head>" +
  "<body style=\"font-family:system-ui;padding:2rem\"><h1>403 &mdash; not allowed</h1>" +
  "<p>Your account is not in the Poseidon sales group. Ask an administrator to add you.</p>" +
  "</body></html>";
```

and `import { AuthError, IdentityConfigError, SALES_ROLE, resolveIdentity } from "./lib/identity";`.

`web/src/lib/request-identity.ts`:

```ts
import { SALES_ROLE } from "./identity";

/**
 * The role half, for handlers and pages. The proxy already answered 403 to
 * any request lacking the role, so reaching here without it is the same
 * class of fault as a missing sub: the proxy did not run. Throw, never render.
 */
export function requireSales(h: Headers | ReadonlyHeaders): { sub: string; roles: string[] } {
  const sub = requireSub(h);
  const roles = (h.get("x-poseidon-roles") ?? "").split(",").filter((r) => r !== "");
  if (!roles.includes(SALES_ROLE)) {
    throw new Error(
      `x-poseidon-roles lacks ${SALES_ROLE}: the proxy should have answered 403 before this request reached a handler`,
    );
  }
  return { sub, roles };
}
```

`web/src/app/page.tsx` and `web/src/app/c/[id]/page.tsx`: replace `const sub = requireSub(await headers());` with `const { sub } = requireSales(await headers());` and the import accordingly.

- [ ] **Step 4: Run to verify pass; full suite; tsc; commit**

```bash
git add web/src/lib/identity.ts web/src/proxy.ts web/src/proxy.test.ts web/src/lib/request-identity.ts web/src/lib/request-identity.test.ts web/src/app/page.tsx "web/src/app/c/[id]/page.tsx"
git commit -m "feat(web): require Poseidon:Sales in the proxy and in every page (P16-4)"
```

---

### Task 6: The corrected Drizzle schema (the source, not the pull)

**Files:**
- Modify: `web/src/db/schema.ts` (whole file: header, roles, indexes, policies, `alembic_version` removed)
- Test: `web/src/db/schema.test.ts` (extend)

**Interfaces:**
- Consumes: migrations `backend/migrations/versions/0003_run_log.py` … `0010_app_role_membership.py` as the reference (read them; never the Phase 15 pull).
- Produces: `schema.ts` exporting `poseidonApp`, `poseidonAdmin`, `poseidonWorker` (`pgRole(...).existing()`), `OWNER_PREDICATE`, and the nine app tables with `.enableRLS()` and their fourteen policies. Task 7 generates from this file.

Column definitions are kept exactly as the pull captured them (the pull got columns, defaults, checks, foreign keys and unique constraints right; it botched policies, index operator classes, roles and FORCE). Change only what is listed.

- [ ] **Step 1: Write the failing tests**

Extend `web/src/db/schema.test.ts` (keep its existing tests):

```ts
import { getTableConfig } from "drizzle-orm/pg-core";
import * as schema from "./schema";

const RLS_TABLES = [
  "conversations", "messages", "turn_run", "llm_calls", "tool_calls",
  "message_feedback", "user_profile", "user_memory", "memory_outbox",
] as const;

describe("schema.ts is the migration source (Phase 16 Task 6)", () => {
  it("no longer models alembic_version", () => {
    expect((schema as Record<string, unknown>).alembicVersion).toBeUndefined();
  });

  it("declares the three roles as pre-existing", () => {
    for (const r of [schema.poseidonApp, schema.poseidonAdmin, schema.poseidonWorker]) {
      expect(r).toBeDefined();
    }
  });

  it.each(RLS_TABLES)("%s has RLS enabled and an owner policy with both predicates", (name) => {
    const table = Object.values(schema).find(
      (t) => typeof t === "object" && t !== null && "enableRLS" in (t as object) && getTableConfig(t as never).name === name,
    );
    expect(table, name).toBeDefined();
    const cfg = getTableConfig(table as never);
    expect(cfg.enableRLS).toBe(true);
    const owner = cfg.policies.find((p) => p.name === `${name}_owner`);
    expect(owner, `${name}_owner`).toBeDefined();
    expect(owner!.using).toBeDefined();
    expect(owner!.withCheck).toBeDefined();
  });

  it("carries the four admin read policies and the worker policy", () => {
    const names = Object.values(schema)
      .filter((t) => typeof t === "object" && t !== null && "enableRLS" in (t as object))
      .flatMap((t) => getTableConfig(t as never).policies.map((p) => p.name));
    for (const n of ["turn_run_admin_read", "llm_calls_admin_read", "tool_calls_admin_read", "message_feedback_admin_read", "memory_outbox_worker"]) {
      expect(names).toContain(n);
    }
    expect(names.filter((n) => n.endsWith("_owner"))).toHaveLength(9);
  });

  it("indexes carry no operator classes", () => {
    for (const t of Object.values(schema)) {
      if (typeof t !== "object" || t === null || !("enableRLS" in (t as object))) continue;
      for (const idx of getTableConfig(t as never).indexes) {
        for (const col of idx.config.columns) {
          expect((col as { op?: string }).op ?? (col as { indexConfig?: { opClass?: string } }).indexConfig?.opClass).toBeUndefined();
        }
      }
    }
  });
});
```

If `getTableConfig`'s shape for policies/indexes differs in drizzle-orm 0.45 from the property names above (`cfg.policies`, `idx.config.columns`), the implementer adjusts the accessors to the real ones and says so in the report; the assertions' intent is fixed.

- [ ] **Step 2: Run to verify failure**

`cd web && npx vitest run src/db/schema.test.ts`. Expected: `alembicVersion` is defined; roles undefined; five owner policies have no `using`; opclasses present. Right failure: the file is still the pull.

- [ ] **Step 3: Rewrite the header**

Replace the whole header comment (lines 1-36) with:

```ts
// The migration SOURCE for the application schema (decision Q3, applied in
// Phase 16). `drizzle-kit generate` reads this file; `web/drizzle/` is what it
// produced. Hand-maintained from here on.
//
// Provenance: columns, defaults, CHECKs, foreign keys and unique constraints
// were introspected from an Alembic-migrated database by drizzle-kit 0.31.10
// (Phase 15). Policies, index definitions and roles were RE-DERIVED BY HAND
// from backend/migrations/versions/0003-0010 in Phase 16 Task 6, because the
// introspection dropped the USING/WITH CHECK predicate on five tables and
// rotated index operator classes. Never re-pull over this file.
//
// What lives OUTSIDE this file, in web/drizzle/0000_roles.sql and
// web/drizzle/0002_grants_force_rls.sql, because Drizzle cannot express it:
// idempotent role creation (roles are cluster-wide), FORCE ROW LEVEL
// SECURITY, every GRANT, and the two GRANT ... TO CURRENT_USER memberships
// from migrations 0009 and 0010.
//
// The `synthetic` schema (migration 0002) is Python's alone and is created by
// backend/poseidon/scripts/seed_synthetic.py, not here.
//
// Tripwires: web/src/db/migrations.test.ts (generate produces nothing new)
// and backend/tests/test_rls_policies.py (-m pg) against a Drizzle-migrated
// database.
```

- [ ] **Step 4: Roles and the shared predicate**

After the imports (add `pgRole` and `index` are already imported; add `pgRole`):

```ts
import { pgTable, index, foreignKey, unique, pgPolicy, pgRole, check, uuid, text, integer, jsonb, timestamp, boolean, primaryKey } from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";

// Created by web/drizzle/0000_roles.sql with idempotent DO blocks (migrations
// 0004, 0005, 0009). `.existing()` keeps drizzle-kit from emitting CREATE ROLE.
export const poseidonApp = pgRole("poseidon_app").existing();
export const poseidonAdmin = pgRole("poseidon_admin").existing();
export const poseidonWorker = pgRole("poseidon_worker").existing();

// Migrations 0004:_OWNER_PREDICATE, verbatim. Postgres stores it as
// `(user_sub = current_setting('app.user_sub'::text, true))`; both spellings
// compare equal in pg_policies, which backend/tests/test_rls_policies.py pins.
export const OWNER_PREDICATE = sql`user_sub = current_setting('app.user_sub', true)`;

const owner = (name: string) =>
  pgPolicy(`${name}_owner`, { as: "permissive", for: "all", using: OWNER_PREDICATE, withCheck: OWNER_PREDICATE });
const adminRead = (name: string) =>
  pgPolicy(`${name}_admin_read`, { as: "permissive", for: "select", to: poseidonAdmin, using: sql`true` });
```

Delete the `alembicVersion` table and the `varchar` import.

- [ ] **Step 5: Per-table edits**

For every table below, replace its policy entries with the helpers, fix its indexes, and append `.enableRLS()` to the `pgTable(...)` call. The `to: ["public"]` the pull wrote is dropped (no `to` = PUBLIC, which is what the Alembic `CREATE POLICY` without `TO` meant).

| Table | Indexes (exact) | Policies |
|---|---|---|
| `toolCalls` | `index("ix_tool_calls_turn_run_id_seq").on(table.turnRunId, table.seq)` | `adminRead("tool_calls")`, `owner("tool_calls")` |
| `memoryOutbox` | none | `pgPolicy("memory_outbox_worker", { as: "permissive", for: "all", to: poseidonWorker, using: sql\`true\`, withCheck: sql\`true\` })`, `owner("memory_outbox")` |
| `userProfile` | none | `owner("user_profile")` |
| `messages` | `index("ix_messages_conversation_order").on(table.conversationId, table.createdAt, table.id)` | `owner("messages")` |
| `conversations` | `index("ix_conversations_user_recency").on(table.userSub, table.updatedAt.desc(), table.id.desc())` | `owner("conversations")` |
| `turnRun` | `index("ix_turn_run_conversation_id_turn_index").on(table.conversationId, table.turnIndex)`, `index("ix_turn_run_created_at").on(table.createdAt)` | `adminRead("turn_run")`, `owner("turn_run")` |
| `llmCalls` | `index("ix_llm_calls_turn_run_id_seq").on(table.turnRunId, table.seq)` | `adminRead("llm_calls")`, `owner("llm_calls")` |
| `messageFeedback` | none (the unique constraint stays) | `adminRead("message_feedback")`, `owner("message_feedback")` |
| `userMemory` | none | `owner("user_memory")` |

Everything else in each table (columns, `foreignKey`, `unique`, `check`, `primaryKey`) stays byte-identical to the pull.

- [ ] **Step 6: Run to verify pass; tsc; commit**

`cd web && npx vitest run src/db/schema.test.ts && npx tsc --noEmit`. Then:

```bash
git add web/src/db/schema.ts web/src/db/schema.test.ts
git commit -m "feat(db): schema.ts becomes the migration source; policies, indexes and roles re-derived from Alembic 0003-0010"
```

---

### Task 7: Drizzle migrations — roles, generated baseline, grants + FORCE RLS — and the tripwire

**Files:**
- Modify: `web/drizzle.config.ts` (add `out`)
- Create: `web/drizzle/0000_roles.sql`, `web/drizzle/0001_baseline.sql` (generated), `web/drizzle/0002_grants_force_rls.sql`, `web/drizzle/meta/*` (generated)
- Test: `web/src/db/migrations.test.ts` (new)

**Interfaces:**
- Consumes: Task 6's schema.
- Produces: a `web/drizzle/` folder that `npx drizzle-kit migrate` applies to an empty database to reproduce migrations 0003–0010's end state. Task 8 wires it into boot.

**Order matters:** policies in the generated baseline say `TO poseidon_admin` / `TO poseidon_worker`, so the roles must exist first (0000), and grants need the tables, so they come last (0002).

- [ ] **Step 1: Write the failing tripwire test**

Create `web/src/db/migrations.test.ts`:

```ts
import { execSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = join(__dirname, "..", "..");
const drizzleDir = join(webRoot, "drizzle");

describe("web/drizzle is in step with schema.ts (Phase 16 Task 7 tripwire)", () => {
  it("contains the three expected migrations and a journal", () => {
    const files = readdirSync(drizzleDir).filter((f) => f.endsWith(".sql")).sort();
    expect(files).toEqual(["0000_roles.sql", "0001_baseline.sql", "0002_grants_force_rls.sql"]);
    expect(existsSync(join(drizzleDir, "meta", "_journal.json"))).toBe(true);
  });

  it("the baseline carries all fourteen policies with predicates and no alembic_version", () => {
    const sql = readFileSync(join(drizzleDir, "0001_baseline.sql"), "utf8");
    expect(sql.match(/CREATE POLICY/g)?.length).toBe(14);
    expect(sql.match(/current_setting\('app\.user_sub', true\)/g)?.length).toBe(18); // 9 USING + 9 WITH CHECK
    expect(sql).not.toContain("alembic_version");
    expect(sql.match(/ENABLE ROW LEVEL SECURITY/g)?.length).toBe(9);
  });

  it("drizzle-kit generate against the committed schema produces nothing new", () => {
    const before = readdirSync(drizzleDir).filter((f) => f.endsWith(".sql")).sort();
    const journalBefore = readFileSync(join(drizzleDir, "meta", "_journal.json"), "utf8");
    const out = execSync("npx drizzle-kit generate", { cwd: webRoot, encoding: "utf8", shell: true } as never);
    const after = readdirSync(drizzleDir).filter((f) => f.endsWith(".sql")).sort();
    expect(after, out).toEqual(before);
    expect(readFileSync(join(drizzleDir, "meta", "_journal.json"), "utf8")).toBe(journalBefore);
  });
});
```

(`execSync` on Windows needs `shell: true` for `npx`; the `as never` cast covers the type of that option if `@types/node` complains. If a stray migration IS produced by the third test, delete it by hand before re-running; the test itself must not delete files.)

- [ ] **Step 2: Run to verify failure**

`cd web && npx vitest run src/db/migrations.test.ts`. Expected: `ENOENT` reading `web/drizzle`. Right failure: nothing generated yet.

- [ ] **Step 3: Point drizzle-kit at an output folder**

In `web/drizzle.config.ts`, add `out: "./drizzle",` after `schema:`. Keep `schemaFilter: ["public"]`.

- [ ] **Step 4: Generate 0000_roles (custom) and fill it**

Run `cd web && npx drizzle-kit generate --custom --name=roles`. Confirm it created `web/drizzle/0000_roles.sql` (empty) and `web/drizzle/meta/_journal.json`. If the layout on disk differs from `NNNN_name.sql` + `meta/_journal.json`, STOP and report the real layout before continuing; the tripwire test's expectations are written for that layout.

Fill `web/drizzle/0000_roles.sql`:

```sql
-- Phase 16 Task 7. Roles are CLUSTER-wide: a plain CREATE ROLE fails when a
-- sibling database in the same cluster already created one, so each is
-- idempotent -- the same DO-block discipline migrations 0004, 0005 and 0009
-- used. schema.ts declares them with pgRole(...).existing().
DO $$ BEGIN CREATE ROLE poseidon_app NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE ROLE poseidon_admin NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE ROLE poseidon_worker NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
```

- [ ] **Step 5: Generate the baseline**

Run `cd web && npx drizzle-kit generate --name=baseline`. Open `web/drizzle/0001_baseline.sql` and verify by reading: 9 `CREATE TABLE`, 14 `CREATE POLICY` (9 owner with `USING (...) WITH CHECK (...)`, 4 `FOR SELECT TO "poseidon_admin" USING (true)`, 1 `TO "poseidon_worker" USING (true) WITH CHECK (true)`), 9 `ENABLE ROW LEVEL SECURITY`, 6 `CREATE INDEX` with no `_ops`, no `alembic_version`, no `CREATE ROLE`. If a policy came out without its predicate, the schema (Task 6) is wrong, not this step: fix the schema, delete the generated files and journal entry, regenerate. Do not hand-edit the generated SQL.

- [ ] **Step 6: Generate 0002_grants_force_rls (custom) and fill it**

Run `cd web && npx drizzle-kit generate --custom --name=grants_force_rls`. Fill `web/drizzle/0002_grants_force_rls.sql` — every statement is transcribed from the named migration:

```sql
-- Phase 16 Task 7: what Drizzle cannot express, transcribed from
-- backend/migrations/versions/0004-0010. Order: FORCE RLS, grants, memberships.

-- FORCE ROW LEVEL SECURITY (0004 conversations/messages; 0005 turn_run/llm_calls/tool_calls;
-- 0006 message_feedback; 0008 user_profile/user_memory/memory_outbox): the table OWNER is
-- filtered too, which is what makes test_owner_connection_is_still_filtered_by_force_rls pass.
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE turn_run FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE llm_calls FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE tool_calls FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE message_feedback FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE user_profile FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE user_memory FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE memory_outbox FORCE ROW LEVEL SECURITY;
--> statement-breakpoint

-- Schema usage (0004 app, 0005 admin, 0009 worker).
GRANT USAGE ON SCHEMA public TO poseidon_app;
--> statement-breakpoint
GRANT USAGE ON SCHEMA public TO poseidon_admin;
--> statement-breakpoint
GRANT USAGE ON SCHEMA public TO poseidon_worker;
--> statement-breakpoint

-- poseidon_app DML. Deliberate gaps: no DELETE on the audit tables (0005: rows are redacted,
-- never removed) nor on message_feedback (0006); no UPDATE on user_memory (0008: versions are
-- immutable).
GRANT SELECT, INSERT, UPDATE, DELETE ON conversations, messages TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON turn_run, llm_calls, tool_calls TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON message_feedback TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON user_profile TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, DELETE ON user_memory TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON memory_outbox TO poseidon_app;
--> statement-breakpoint

-- poseidon_admin read (0005, 0006). Nothing on the personalization tables (0008, by design).
GRANT SELECT ON turn_run, llm_calls, tool_calls TO poseidon_admin;
--> statement-breakpoint
GRANT SELECT ON message_feedback TO poseidon_admin;
--> statement-breakpoint

-- poseidon_worker (0009).
GRANT SELECT, UPDATE ON memory_outbox TO poseidon_worker;
--> statement-breakpoint

-- Memberships (0009, 0010): a non-superuser DSN can only SET ROLE to a role it is a member of.
-- CURRENT_USER is whoever runs `drizzle-kit migrate`, exactly as it was whoever ran alembic.
GRANT poseidon_worker TO CURRENT_USER;
--> statement-breakpoint
GRANT poseidon_app TO CURRENT_USER;
```

Cross-check each GRANT line against the migration it cites before moving on; write "verified against 000N" per block in the report.

- [ ] **Step 7: Run the tripwire; apply to a scratch database**

`cd web && npx vitest run src/db/migrations.test.ts` → 3 passed.

Then prove it applies. With the 5434 db up, create a scratch database and migrate it:

```powershell
docker exec -i $(docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml ps -q db) psql -U poseidon -d poseidon -c "DROP DATABASE IF EXISTS poseidon_drizzle; CREATE DATABASE poseidon_drizzle;"
cd web; $env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5434/poseidon_drizzle"; npx drizzle-kit migrate
```

Expected: three migrations applied, no error. Then `psql ... -d poseidon_drizzle -c "\dp"` style checks are Task 8's job (the pg suite). Here, only confirm `SELECT count(*) FROM pg_policies` returns 14 and `SELECT relforcerowsecurity FROM pg_class WHERE relname='messages'` returns `t`.

- [ ] **Step 8: Commit**

```bash
git add web/drizzle.config.ts web/drizzle web/src/db/migrations.test.ts
git commit -m "feat(db): Drizzle migrations 0000_roles, 0001_baseline, 0002_grants_force_rls re-derived from Alembic 0003-0010"
```

---

### Task 8: Boot order — synthetic schema via the seed, compose migrate service, Alembic retired, pg gate

**Files:**
- Create: `backend/poseidon/core/data/synthetic_schema.py`, `backend/tests/test_synthetic_schema.py`, `backend/tests/fixtures/synthetic_schema_columns.json`
- Modify: `backend/poseidon/scripts/seed_synthetic.py`, `backend/poseidon/core/db.py` (three strings at ~594, ~608, ~624), `backend/tests/test_boot_privileges.py` (only assertions that pin those strings), `infra/docker-compose.yml`, `CLAUDE.md` (lines 15, 19, 61 and the "Migrations" command block)
- Gate: the pg suite against a Drizzle-only database

**Interfaces:**
- Produces: `ensure_synthetic_schema(conn: psycopg.Connection) -> None`; compose `migrate` service; `backend` no longer runs Alembic.

- [ ] **Step 1: Capture the reference column set BEFORE anything changes**

With the 5434 db (Alembic-migrated) up, run from `backend/`:

```powershell
$env:DATABASE_URL="postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon"
.venv/Scripts/python.exe -c "import json, psycopg; from poseidon.core.data.synthetic_client import normalize_dsn; import os; c = psycopg.connect(normalize_dsn(os.environ['DATABASE_URL'])); rows = c.execute(\"SELECT table_name, column_name, data_type, numeric_precision, numeric_scale, ordinal_position FROM information_schema.columns WHERE table_schema='synthetic' ORDER BY table_name, ordinal_position\").fetchall(); idx = c.execute(\"SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='synthetic' ORDER BY indexname\").fetchall(); json.dump({'columns': rows, 'indexes': idx}, open('tests/fixtures/synthetic_schema_columns.json','w'), indent=1)"
```

Commit this fixture as the first commit of the task; it is the ground truth the new DDL must reproduce.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_synthetic_schema.py`:

```python
"""Phase 16 Task 8: the `synthetic` schema is created by the seed path, not by
Alembic, now that Drizzle owns migrations. Two pins: the column dicts stay
byte-equal to migration 0002's (offline), and the DDL reproduces what 0002
produced on a real database (pg)."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from poseidon.core.data.synthetic_schema import GL_COLUMNS, SALES_COLUMNS, ensure_synthetic_schema

_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0002_synthetic_schema.py"
_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_schema_columns.json"


def _load_0002():
    spec = importlib.util.spec_from_file_location("m0002", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_column_dicts_match_migration_0002_exactly():
    m = _load_0002()
    assert SALES_COLUMNS == m.SALES_COLUMNS
    assert GL_COLUMNS == m.GL_COLUMNS


@pytest.mark.pg
def test_ddl_reproduces_the_alembic_schema_on_a_scratch_database():
    import psycopg

    from poseidon.core.data.synthetic_client import normalize_dsn

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip("DATABASE_URL unset")
    admin = psycopg.connect(normalize_dsn(dsn), autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS poseidon_synth_probe")
    admin.execute("CREATE DATABASE poseidon_synth_probe")
    probe_dsn = normalize_dsn(dsn).rsplit("/", 1)[0] + "/poseidon_synth_probe"
    with psycopg.connect(probe_dsn) as conn:
        ensure_synthetic_schema(conn)
        ensure_synthetic_schema(conn)  # idempotent
        conn.commit()
        rows = conn.execute(
            "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale, "
            "ordinal_position FROM information_schema.columns WHERE table_schema='synthetic' "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
        idx = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='synthetic' ORDER BY indexname"
        ).fetchall()
    expected = json.loads(_FIXTURE.read_text())
    assert [list(r) for r in rows] == expected["columns"]
    assert [list(r) for r in idx] == expected["indexes"]
    admin.execute("DROP DATABASE poseidon_synth_probe")
```

- [ ] **Step 3: Run to verify failure**

Offline: `ModuleNotFoundError: poseidon.core.data.synthetic_schema`. Right failure.

- [ ] **Step 4: Write the module**

Create `backend/poseidon/core/data/synthetic_schema.py`. Copy `SALES_COLUMNS`, `GL_COLUMNS`, `_QUOTED_COLUMNS` verbatim from `backend/migrations/versions/0002_synthetic_schema.py` (the dicts, byte for byte — `test_column_dicts_match_migration_0002_exactly` enforces it) and translate `_PG_TYPES` from SQLAlchemy types to the SQL spellings `information_schema` reports for them (read 0002's `_PG_TYPES` and the fixture from Step 1 side by side; e.g. `sa.Text()` → `text`, `sa.Date()` → `date`, `sa.Numeric(18, 4)` → `numeric(18,4)`, `sa.Integer()` → `integer`). Then:

```python
SCHEMA = "synthetic"
SALES_TABLE = "marine_sales_planning_v"
GL_TABLE = "w_marine_gl_source_ai"

_INDEXES = (
    ("ix_marine_sales_planning_v_lift_eta_date", SALES_TABLE, "lift_eta_date"),
    ("ix_marine_sales_planning_v_cust_nm", SALES_TABLE, "cust_nm"),
    ("ix_marine_sales_planning_v_loc_nm", SALES_TABLE, "loc_nm"),
    ("ix_w_marine_gl_source_ai_period_date", GL_TABLE, "period_date"),
    ("ix_w_marine_gl_source_ai_class4", GL_TABLE, "class4"),
)


def _column_sql(ontology_columns: dict[str, str]) -> str:
    parts = []
    for name, pg_type in ontology_columns.items():
        col = f'"{name}"' if name in _QUOTED_COLUMNS else name.lower()
        parts.append(f"{col} {_PG_TYPES[pg_type]}")
    return ", ".join(parts)


def ensure_synthetic_schema(conn) -> None:
    """Idempotent DDL for migration 0002's schema. Every statement is IF NOT
    EXISTS, so running it against a database 0002 already migrated is a no-op
    and running it against a Drizzle-only database creates the schema."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{SALES_TABLE} ({_column_sql(SALES_COLUMNS)})")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{GL_TABLE} ({_column_sql(GL_COLUMNS)})")
    for index_name, table, column in _INDEXES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {SCHEMA}.{table} ({column})")
```

(Column names in `_INDEXES` are the lower-cased names 0002 used; if any indexed column is in `_QUOTED_COLUMNS`, quote it the same way `_column_sql` does.)

- [ ] **Step 5: Call it from the seed and retire the hint**

In `backend/poseidon/scripts/seed_synthetic.py` `main`, right after `psycopg.connect(...)` succeeds and before `_row_counts(conn)`:

```python
            ensure_synthetic_schema(conn)
            conn.commit()
```

Change `_MIGRATE_HINT` to `"the seed step creates the synthetic schema itself now (Phase 16); an UndefinedTable here means ensure_synthetic_schema failed - read the error above"` and keep the `except UndefinedTable` branch as a fallback.

- [ ] **Step 6: The three db.py strings and their test pins**

In `backend/poseidon/core/db.py` replace, in the three RuntimeError messages at `_assert_app_role` (two) and `_assert_worker_role` (one), the phrase ``run `python -m alembic upgrade head` against this database (migration 0004 creates poseidon_app)`` / ``(migration 0010 issues GRANT ...)`` / ``(migration 0009 creates ...)`` with ``run `npx drizzle-kit migrate` in web/ against this database (Drizzle migration 0000_roles creates poseidon_app)`` / ``(0002_grants_force_rls issues GRANT {app_role} TO CURRENT_USER)`` / ``(0000_roles creates {WORKER_ROLE}; 0002_grants_force_rls grants it on memory_outbox and grants membership to the migrating DSN user)``. Also the `_run_checks` message's "(the role migration 0004 creates)" → "(Drizzle migration 0000_roles creates it)". Then grep `backend/tests` for `alembic upgrade head` and update only assertions that pin these exact strings (`test_boot_privileges.py`); skip messages in other files are left alone.

- [ ] **Step 7: Compose**

In `infra/docker-compose.yml`, add after `minio`:

```yaml
  # Phase 16 Task 8 (decision D58): Drizzle owns the application schema.
  # This one-shot service is the ONLY thing that migrates; `backend` and
  # `worker` wait for it to complete. Alembic no longer runs at boot --
  # backend/migrations/versions/0001-0010 are frozen history, and the
  # `synthetic` schema (0002) is created by the seed step below.
  migrate:
    image: node:24-alpine
    working_dir: /app
    command: sh -c "npm ci && npx drizzle-kit migrate"
    environment:
      DRIZZLE_DATABASE_URL: postgresql://poseidon:poseidon@db:5432/poseidon
    volumes: ["../web:/app", "/app/node_modules"]
    depends_on:
      db: { condition: service_healthy }
```

`backend`: command becomes `sh -c "python -m poseidon.scripts.seed_synthetic && python -m uvicorn ..."` (Alembic line removed); add `POSEIDON_INTERNAL_TOKEN: dev-internal-token` to its environment; `depends_on` gains `migrate: { condition: service_completed_successfully }`. `worker`: `depends_on` gains the same `migrate` condition and its comment block's Alembic sentences are rewritten to name the migrate service. Update the `worker` comment "backend's start-up command owns alembic upgrade head" accordingly.

- [ ] **Step 8: CLAUDE.md**

Line 15: `alembic` → `alembic (frozen; not run at boot)`. Line 19: `Alembic at revision **0010**` → `Alembic frozen at **0010** (history only) · **Drizzle migrates** (`web/drizzle`, D58)`. The "Migrations (compose runs this on boot)" block becomes:

```bash
# Migrations (compose's `migrate` service runs this on boot; Drizzle is the authority since Phase 16)
cd web && npx drizzle-kit migrate      # reads DRIZZLE_DATABASE_URL
# Fresh local database after the Phase 16 cutover: docker compose -f infra/docker-compose.yml down -v
```

- [ ] **Step 9: The gate — pg suite against a Drizzle-only database**

```powershell
docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml down -v
docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml up -d db
cd web; $env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5434/poseidon"; npx drizzle-kit migrate; cd ..
cd backend; $env:DATABASE_URL="postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon"; .venv/Scripts/python.exe -m poseidon.scripts.seed_synthetic
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg > ../.superpowers/sdd/2026-09-19-phase-16-chat-parity/pytest-t8-pg.log 2>&1; Get-Content ../.superpowers/sdd/2026-09-19-phase-16-chat-parity/pytest-t8-pg.log -Tail 8
```

Expected: every test passes (last stale figure 334 passed / 1 skipped; report the real numbers). `alembic_version` does not exist on this database; if any pg test reads it, report the test and stop — that is a design gap, not something to patch. Then run the offline suite and ruff as usual.

- [ ] **Step 10: Commit (two commits)**

```bash
git add backend/tests/fixtures/synthetic_schema_columns.json
git commit -m "test: capture migration 0002's synthetic schema as the reference for the seed-created DDL"
git add backend/poseidon/core/data/synthetic_schema.py backend/poseidon/scripts/seed_synthetic.py backend/poseidon/core/db.py backend/tests/test_synthetic_schema.py backend/tests/test_boot_privileges.py infra/docker-compose.yml CLAUDE.md
git commit -m "feat(infra): Drizzle migrates at boot, Alembic retired, synthetic schema created by the seed (D58)"
```

---

### Task 9: Python shared `stream_turn` and the internal turns route

**Files:**
- Modify: `backend/poseidon/api/live_chat.py:751-1069` (extract), `backend/poseidon/api/auth.py` (two helpers), `backend/poseidon/api/internal.py` (new route)
- Test: `backend/tests/test_internal_turns.py` (new)

**Interfaces:**
- Consumes: Task 1's router gate; `test_live_chat_sse.py`'s `_live_app`, `read_sse`, `FakeDataClient`, `RecordingWriter` (imported, not copied).
- Produces: `live_chat.stream_turn(*, cid: str, text: str, client_turn_key: str | None, user: UserContext, app_state) -> StreamingResponse` (async); `auth.assert_sales(user: UserContext) -> UserContext`; `auth.check_chat_rate_limit(request: Request, key: str) -> None`; `POST /internal/v1/conversations/{cid}/turns` with body `{identity: {sub, roles}, text, client_turn_key?}` streaming the identical frames. Task 10's scenarios and Task 14's client rely on the route.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_internal_turns.py`:

```python
"""Phase 16 Task 9: the internal turns route is the public send route with
identity taken from the body (spec section 5.1). The proof is byte-level:
the same scripted turn through both routes yields the same frame sequence."""

import pytest
from fastapi.testclient import TestClient

from tests.test_chat_orchestrator import FakeDataClient, RecordingWriter
from tests.test_live_chat_sse import _live_app, pg_database_url, read_sse  # noqa: F401

_TOKEN = "test-internal-token"
_HEADERS = {"X-Poseidon-Internal-Token": _TOKEN}
_SALES = {"sub": "dev|local", "roles": ["Poseidon:Sales"]}
_PATH = "/internal/v1/conversations/{cid}/turns"


def _app(**overrides):
    return _live_app(data_client=FakeDataClient(), writer=RecordingWriter(), internal_token=_TOKEN, **overrides)


def _strip(frames):
    """(name, data) minus the per-request ids, for cross-route comparison."""
    out = []
    for name, data in frames:
        out.append((name, {k: v for k, v in data.items() if k not in ("turn_id", "message_id")}))
    return out


def test_internal_turn_requires_the_token():
    client = TestClient(_app())
    resp = client.post(_PATH.format(cid="00000000-0000-0000-0000-000000000000"), json={"identity": _SALES, "text": "hi"})
    assert resp.status_code == 401


def test_internal_turn_requires_the_sales_role():
    client = TestClient(_app(), headers=_HEADERS)
    resp = client.post(
        _PATH.format(cid="00000000-0000-0000-0000-000000000000"),
        json={"identity": {"sub": "dev|nobody", "roles": []}, "text": "hi"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "caller lacks required role 'Poseidon:Sales'"


def test_internal_turn_in_mock_chat_mode_is_503():
    from tests.test_live_chat_sse import _mock_app  # mock mode: no history store wired

    app = _mock_app()
    app.state.settings = app.state.settings.model_copy(update={"internal_token": _TOKEN})
    resp = TestClient(app, headers=_HEADERS).post(
        _PATH.format(cid="00000000-0000-0000-0000-000000000000"), json={"identity": _SALES, "text": "hi"}
    )
    assert resp.status_code == 503


@pytest.mark.pg
def test_internal_turn_404s_for_an_unknown_conversation(pg_database_url):
    client = TestClient(_app(database_url=pg_database_url), headers=_HEADERS)
    resp = client.post(_PATH.format(cid="00000000-0000-0000-0000-000000000000"), json={"identity": _SALES, "text": "hi"})
    assert resp.status_code == 404


@pytest.mark.pg
def test_internal_and_public_routes_stream_the_same_frames(pg_database_url):
    """The flagship scripted turn (`test_live_turn_streams_the_flagship_frame_
    sequence_and_table_and_proof_parts`) through both routes."""
    app = _app(database_url=pg_database_url)
    client = TestClient(app, headers=_HEADERS)
    text = "top customers by gross profit in Singapore"  # the flagship text used by test_live_chat_sse

    cid_public = client.post("/api/conversations").json()["conversation"]["id"]
    public = _strip(read_sse(client, cid_public, text, "key-public", {}))

    cid_internal = client.post("/api/conversations").json()["conversation"]["id"]
    with client.stream(
        "POST",
        _PATH.format(cid=cid_internal),
        json={"identity": _SALES, "text": text, "client_turn_key": "key-internal"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        internal = _strip(_parse(resp.iter_lines()))

    assert internal == public
    assert [n for n, _ in internal][0] == "accepted"
    assert [n for n, _ in internal][-1] == "done"
```

Add a module-local `_parse(lines)` that decodes `event: `/`data: ` pairs into `(name, dict)` tuples exactly as `read_sse` does (copy its parsing loop; `read_sse` is bound to the public path). Use the flagship text and `client_turn_key` handling that `test_live_chat_sse.py`'s own flagship test uses — read that test first and mirror it; if `read_sse`'s signature differs from the call above, follow the real signature.

- [ ] **Step 2: Run to verify failure**

Offline tests: 404 from FastAPI (route not mounted) instead of 401/403/503. Right failure.

- [ ] **Step 3: Two helpers in auth.py**

```python
def assert_sales(user: UserContext) -> UserContext:
    """The role half of require_sales, callable on an identity that did not
    arrive through the request (the internal turns route builds one from
    its body). Same 403, same detail string."""
    if _REQUIRED_ROLE not in user.roles:
        raise AuthError(403, "insufficient role", f"caller lacks required role {_REQUIRED_ROLE!r}")
    return user


def check_chat_rate_limit(request: Request, key: str) -> None:
    limiter = getattr(request.app.state, "chat_rate_limiter", None)
    if limiter is None:
        return
    retry_after = limiter.check(key)
    if retry_after is not None:
        raise RateLimitExceeded(retry_after_seconds=max(1, math.ceil(retry_after)))
```

`require_sales` becomes `return assert_sales(user)`; `rate_limit_chat_send` computes `key` as today then calls `check_chat_rate_limit(request, key)`.

- [ ] **Step 4: Extract `stream_turn` in live_chat.py**

Turn the body of `send_message` (everything after its docstring, lines ~767-1069) into:

```python
async def stream_turn(
    *, cid: str, text: str, client_turn_key: str | None, user: UserContext, app_state
) -> StreamingResponse:
    """One chat turn as an SSE stream. Shared by the public route below and
    the internal route in api/internal.py (Phase 16 Task 9), so the two can
    never drift: identity is the ONLY thing that differs, and it arrives as
    a parameter. See send_message's docstring for the thread+queue bridge."""
```

Inside, mechanical substitutions: `request.app.state` → `app_state`; `request.state.user` → `user`; `body.text` → `text`; `body.client_turn_key` → `client_turn_key`. `send_message` becomes:

```python
    return await stream_turn(
        cid=cid,
        text=body.text,
        client_turn_key=body.client_turn_key,
        user=request.state.user,
        app_state=request.app.state,
    )
```

No behaviour change; `test_live_chat_sse.py` is the regression net.

- [ ] **Step 5: The internal route**

In `backend/poseidon/api/internal.py`:

```python
class _TurnRequest(BaseModel):
    identity: _Identity
    text: str
    client_turn_key: str | None = None


@router.post("/conversations/{cid}/turns")
async def stream_conversation_turn(cid: str, body: _TurnRequest, request: Request) -> StreamingResponse:
    """The public chat-send route with identity from the body (spec 5.1).
    Requires CHAT_MODE=live: the history store and the rest of the turn
    wiring exist only then."""
    app_state = request.app.state
    if getattr(app_state, "history_store", None) is None:
        raise HTTPException(status_code=503, detail="CHAT_MODE=live is required for internal turns")
    user = assert_sales(
        UserContext(sub=body.identity.sub, email=None, name=None, roles=tuple(body.identity.roles))
    )
    check_chat_rate_limit(request, user.sub)
    return await stream_turn(
        cid=cid, text=body.text, client_turn_key=body.client_turn_key, user=user, app_state=app_state
    )
```

Imports: `from fastapi.responses import StreamingResponse`, `from poseidon.api.auth import assert_sales, check_chat_rate_limit`, `from poseidon.api.live_chat import stream_turn`. Check `app.py`'s import order does not create a cycle (`internal` importing `live_chat` is new; `live_chat` must not import `internal`).

- [ ] **Step 6: Run to verify pass**

Offline: the three offline tests pass; `test_live_chat_sse.py`'s offline tests unchanged. pg (5434 db up, Drizzle-migrated from Task 8): the two pg tests pass and the full pg suite stays green. Offline suite + ruff.

- [ ] **Step 7: Commit**

```bash
git add backend/poseidon/api/live_chat.py backend/poseidon/api/auth.py backend/poseidon/api/internal.py backend/tests/test_internal_turns.py
git commit -m "feat(api): shared stream_turn; internal conversations/{cid}/turns route streams the same frames"
```

---

### Task 10: The contract corpus — scenarios, golden test, opener

**Files:**
- Create: `backend/tests/contracts/__init__.py` (empty), `backend/tests/contracts/scenarios.py`, `backend/tests/contracts/test_turn_contracts.py`, `contracts/turns/*.json`, `contracts/turns/README.md`

**Interfaces:**
- Consumes: `execute_turn`, `SseEnvelopeSink`, `TurnTranscriptBuffer`, `_record_transcript_frame` (live_chat.py:460), `_decode_frame` (446), `_build_opener_parts` (history.py:335), and the helpers in `tests/test_chat_orchestrator.py` (`REGISTRY`, `FakeDataClient`, `RecordingWriter`, `_settings`, `_dev_role_client`, `_capturing_send`, `_sink`).
- Produces: one JSON file per scenario under `contracts/turns/`, shape `{"scenario": str, "frames": [{"event": str, "data": {...}}], "parts": [{"kind", "payload"}], "terminal": {"event": "done"|"error", "data": {...}}}`, plus `contracts/turns/opener.json` = `_build_opener_parts()`. Task 11 and 13 read these. **Spec refinement (§11):** the recorder is the golden test itself in update mode, not a separate script; one JSON per scenario, not two.

- [ ] **Step 1: Write the scenario module and the failing golden test**

`backend/tests/contracts/scenarios.py` defines `SCENARIOS: dict[str, Callable[[pytest.MonkeyPatch], Recorded]]` where `Recorded = tuple[list[str], list[dict]]` (raw frames, persisted parts). Four scenarios run `execute_turn` exactly as the named orchestrator tests arrange it (copy each test's arrange block into a function; do not import the test functions):

| Scenario id | Source test in `test_chat_orchestrator.py` | What it covers |
|---|---|---|
| `metric_query_flagship` | `test_flagship_singapore_top_gp_frame_sequence_and_writer_rows` | accepted, tool start/done, table, proof, text token, done |
| `clarify_ambiguous_customer` | `test_ambiguous_turn_produces_chips_and_text_parts_clarify_status_no_dispatch` | chips + text, done with clarify |
| `error_mid_turn` | `test_error_turn_emits_error_event_and_finalizes_error_state_untouched` | error frame, no done |
| `replay_after_ok` | `test_retry_after_an_ok_turn_replays_the_persisted_parts_instead_of_erroring` | replayed parts then done |

Plus one sink-level scenario `all_part_kinds` built directly on `SseEnvelopeSink` (no orchestrator): `accepted`, a `phase` frame, `tool` start for `tool_seq` 1, `part`s of kind `metric_grid`, `table`, `proof`, `phase_section`, `artifact` (payload `{"name": "brief.pdf", "url": "https://example.invalid/brief.pdf", "mime": "application/pdf"}`), `tool` done, a `part` of kind `text` followed by a `token` (so Task 11's merge rule is exercised), `chips`, `done`. Use the sink's own `emit`/`push_part`/`push_token`/`done` methods as `test_chat_orchestrator.py` does.

Each scenario returns raw frames captured via `_capturing_send()` and the persisted parts obtained by folding the frames through `TurnTranscriptBuffer` + `_record_transcript_frame` (the exact function the public route uses). `scenarios.py` also exposes `to_json(frames, parts) -> dict` using `_decode_frame` per frame and `terminal` = the last frame.

`backend/tests/contracts/test_turn_contracts.py`:

```python
"""The turn contract corpus (spec section 11). Golden files under
contracts/turns/ pin the ordered frame sequence and the persisted parts per
scenario. Regenerate ON PURPOSE with POSEIDON_UPDATE_CONTRACTS=1 and commit
the diff; an unexplained diff here is a contract change."""

import json
import os
from pathlib import Path

import pytest

from poseidon.core.chat.history import _build_opener_parts
from tests.contracts.scenarios import SCENARIOS, to_json

CORPUS = Path(__file__).resolve().parents[3] / "contracts" / "turns"
UPDATE = os.environ.get("POSEIDON_UPDATE_CONTRACTS") == "1"


def _check_or_write(path: Path, payload: dict) -> None:
    rendered = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    assert path.exists(), f"{path} missing: run with POSEIDON_UPDATE_CONTRACTS=1"
    assert path.read_text(encoding="utf-8") == rendered, f"{path.name} drifted from the code"


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
def test_scenario_matches_its_golden_file(scenario_id, monkeypatch):
    frames, parts = SCENARIOS[scenario_id](monkeypatch)
    _check_or_write(CORPUS / f"{scenario_id}.json", {"scenario": scenario_id, **to_json(frames, parts)})


def test_opener_parts_golden():
    _check_or_write(CORPUS / "opener.json", {"parts": _build_opener_parts()})


def test_every_golden_file_has_a_scenario():
    on_disk = {p.stem for p in CORPUS.glob("*.json")} - {"opener"}
    assert on_disk == set(SCENARIOS)
```

- [ ] **Step 2: Run to verify failure**

`cd backend && ... -m pytest tests/contracts -q ...`. Expected: `contracts/turns/<id>.json missing: run with POSEIDON_UPDATE_CONTRACTS=1` for every scenario. Right failure.

- [ ] **Step 3: Generate the corpus**

`POSEIDON_UPDATE_CONTRACTS=1 env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts -q` (PowerShell: set `$env:POSEIDON_UPDATE_CONTRACTS="1"` first, then unset it). Read every generated file: ids are the fixed `turn-1`/`msg-1` the test sink uses, `event_seq` is monotonic from 1, and the `all_part_kinds` file contains every kind. Write `contracts/turns/README.md` (ten lines: what the files are, the regenerate command, "changes only land with a regenerated fixture in the same commit").

- [ ] **Step 4: Run without the env var — pass; offline suite; ruff; commit**

```bash
git add backend/tests/contracts contracts/turns
git commit -m "test(contracts): turn corpus under contracts/turns pinned by a golden test on the Python side"
```

---

### Task 11: Web translator, adapter and the contract test

**Files:**
- Modify: `web/package.json` (add `ai@6.0.286`, `@ai-sdk/react@3.0.289`, `zod@^4`)
- Create: `web/src/lib/turn-stream.ts`, `web/src/lib/message-parts.ts`
- Test: `web/src/lib/turn-stream.test.ts`, `web/src/lib/message-parts.test.ts`, `web/src/lib/turn-contracts.test.ts`

**Interfaces:**
- Consumes: `contracts/turns/*.json` (Task 10); `ai`'s `UIMessageChunk`, `UIMessage`, `readUIMessageStream`.
- Produces (all exported):
  - `turn-stream.ts`: `type EnvelopeFrame = { event: string; data: { turn_id: string; message_id: string; event_seq: number } & Record<string, unknown> }`; `parseSseChunk(buffer: string): { frames: EnvelopeFrame[]; rest: string }`; `createFrameTranslator(): { translate(frame: EnvelopeFrame): UIMessageChunk[]; end(): UIMessageChunk[] }`; `TEXT_PART_ID = "text-1"`.
  - `message-parts.ts`: `type MessagePart = { kind: string; payload: unknown }`; `type PoseidonUIMessage = UIMessage<TurnMetadata, Record<string, unknown>>`; `type TurnMetadata = { turn_id?: string; turn_index?: number; status?: "ok" | "error"; usage?: unknown; title?: string | null }`; `toMessageParts(message: PoseidonUIMessage): MessagePart[]`; `fromMessageParts(parts: MessagePart[]): PoseidonUIMessage["parts"]`.

- [ ] **Step 1: Install**

`cd web && npm install ai@6.0.286 @ai-sdk/react@3.0.289 zod@^4`. Confirm `package.json` pins `"ai": "6.0.286"` (exact) — the 7.x line is `latest` on npm and a caret would not cross majors, but an exact pin says the decision out loud. `npx tsc --noEmit` still clean. Commit the lockfile with the task.

- [ ] **Step 2: Write the failing tests**

`web/src/lib/turn-stream.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { createFrameTranslator, parseSseChunk, TEXT_PART_ID, type EnvelopeFrame } from "./turn-stream";

const env = { turn_id: "turn-1", message_id: "msg-1" };
const f = (event: string, seq: number, extra: Record<string, unknown> = {}): EnvelopeFrame => ({
  event,
  data: { ...env, event_seq: seq, ...extra },
});

describe("parseSseChunk", () => {
  it("parses id/event/data frames and keeps a partial trailing block", () => {
    const raw = 'id: 1\nevent: accepted\ndata: {"turn_id":"t","message_id":"m","event_seq":1,"turn_index":0}\n\nid: 2\nevent: tok';
    const { frames, rest } = parseSseChunk(raw);
    expect(frames).toEqual([{ event: "accepted", data: { turn_id: "t", message_id: "m", event_seq: 1, turn_index: 0 } }]);
    expect(rest).toBe("id: 2\nevent: tok");
  });
});

describe("createFrameTranslator", () => {
  it("accepted -> start with the message id and turn metadata", () => {
    const t = createFrameTranslator();
    expect(t.translate(f("accepted", 1, { turn_index: 3 }))).toEqual([
      { type: "start", messageId: "msg-1", messageMetadata: { turn_id: "turn-1", turn_index: 3 } },
    ]);
  });

  it("tool frames become one reconciled data-tool_event part keyed by tool_seq", () => {
    const t = createFrameTranslator();
    const start = t.translate(f("tool", 2, { tool_seq: 1, tool: "x", server: null, status: "start", label: "X" }));
    const done = t.translate(f("tool", 3, { tool_seq: 1, tool: "x", server: null, status: "done", label: "X" }));
    expect(start).toEqual([{ type: "data-tool_event", id: "tool-1", data: { tool_seq: 1, tool: "x", server: null, status: "start", label: "X" } }]);
    expect(done[0]).toMatchObject({ type: "data-tool_event", id: "tool-1", data: { status: "done" } });
  });

  it("phase frames are transient", () => {
    const t = createFrameTranslator();
    expect(t.translate(f("phase", 2, { phase: "contextualize", status: "running" }))).toEqual([
      { type: "data-phase", id: "contextualize", data: { phase: "contextualize", status: "running" }, transient: true },
    ]);
  });

  it("part frames become data-<kind> with the payload verbatim", () => {
    const t = createFrameTranslator();
    expect(t.translate(f("part", 2, { kind: "table", payload: { columns: ["a"], rows: [[1]] } }))).toEqual([
      { type: "data-table", data: { columns: ["a"], rows: [[1]] } },
    ]);
  });

  it("tokens open one text part and done closes it with status ok", () => {
    const t = createFrameTranslator();
    expect(t.translate(f("token", 2, { text: "Hel" }))).toEqual([
      { type: "text-start", id: TEXT_PART_ID },
      { type: "text-delta", id: TEXT_PART_ID, delta: "Hel" },
    ]);
    expect(t.translate(f("token", 3, { text: "lo" }))).toEqual([{ type: "text-delta", id: TEXT_PART_ID, delta: "lo" }]);
    expect(t.translate(f("done", 4, { usage: { input_tokens: 1 }, title: "T" }))).toEqual([
      { type: "text-end", id: TEXT_PART_ID },
      { type: "finish", messageMetadata: { turn_id: "turn-1", status: "ok", usage: { input_tokens: 1 }, title: "T" } },
    ]);
    expect(t.end()).toEqual([]);
  });

  it("error frames become a data-error part then a finish with status error", () => {
    const t = createFrameTranslator();
    expect(t.translate(f("error", 2, { code: "internal_error", message: "boom" }))).toEqual([
      { type: "data-error", data: { code: "internal_error", message: "boom" } },
      { type: "finish", messageMetadata: { turn_id: "turn-1", status: "error" } },
    ]);
  });

  it("end() without a terminal frame emits an error chunk after closing any open text", () => {
    const t = createFrameTranslator();
    t.translate(f("accepted", 1, { turn_index: 0 }));
    t.translate(f("token", 2, { text: "half" }));
    expect(t.end()).toEqual([
      { type: "text-end", id: TEXT_PART_ID },
      { type: "error", errorText: "turn stream ended without a done or error frame" },
    ]);
  });
});
```

`web/src/lib/message-parts.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { fromMessageParts, toMessageParts, type PoseidonUIMessage } from "./message-parts";

describe("toMessageParts", () => {
  it("maps text and data parts to {kind, payload} in order", () => {
    const m = { id: "m", role: "assistant", parts: [
      { type: "data-table", data: { columns: [], rows: [] } },
      { type: "text", text: "hi", state: "done" },
    ] } as unknown as PoseidonUIMessage;
    expect(toMessageParts(m)).toEqual([
      { kind: "table", payload: { columns: [], rows: [] } },
      { kind: "text", payload: { markdown: "hi" } },
    ]);
  });

  it("a streamed text part merges into an immediately preceding text part, as Python's fold_token does", () => {
    const m = { id: "m", role: "assistant", parts: [
      { type: "data-text", data: { markdown: "Ask: " } },
      { type: "text", text: "more" },
    ] } as unknown as PoseidonUIMessage;
    expect(toMessageParts(m)).toEqual([{ kind: "text", payload: { markdown: "Ask: more" } }]);
  });

  it("a data-text part never merges backward", () => {
    const m = { id: "m", role: "assistant", parts: [
      { type: "text", text: "a" },
      { type: "data-text", data: { markdown: "b" } },
    ] } as unknown as PoseidonUIMessage;
    expect(toMessageParts(m)).toEqual([
      { kind: "text", payload: { markdown: "a" } },
      { kind: "text", payload: { markdown: "b" } },
    ]);
  });

  it("skips step markers and phase parts", () => {
    const m = { id: "m", role: "assistant", parts: [
      { type: "step-start" }, { type: "data-phase", data: {} }, { type: "text", text: "x" },
    ] } as unknown as PoseidonUIMessage;
    expect(toMessageParts(m)).toEqual([{ kind: "text", payload: { markdown: "x" } }]);
  });
});

describe("fromMessageParts", () => {
  it("is the inverse for persisted history, keying tool events by tool_seq", () => {
    const parts = [
      { kind: "text", payload: { markdown: "hi" } },
      { kind: "tool_event", payload: { tool_seq: 2, tool: "t", server: null, status: "done", label: "T" } },
    ];
    expect(fromMessageParts(parts)).toEqual([
      { type: "text", text: "hi", state: "done" },
      { type: "data-tool_event", id: "tool-2", data: parts[1].payload },
    ]);
    expect(toMessageParts({ id: "m", role: "assistant", parts: fromMessageParts(parts) } as PoseidonUIMessage)).toEqual(parts);
  });
});
```

`web/src/lib/turn-contracts.test.ts` — the spec §11 replay test:

```ts
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { readUIMessageStream, type UIMessageChunk } from "ai";
import { describe, expect, it } from "vitest";
import { toMessageParts, type PoseidonUIMessage } from "./message-parts";
import { createFrameTranslator, type EnvelopeFrame } from "./turn-stream";

const CORPUS = join(__dirname, "..", "..", "..", "contracts", "turns");
type Golden = { scenario: string; frames: EnvelopeFrame[]; parts: unknown[]; terminal: EnvelopeFrame };

const goldens = readdirSync(CORPUS)
  .filter((f) => f.endsWith(".json") && f !== "opener.json")
  .map((f) => JSON.parse(readFileSync(join(CORPUS, f), "utf8")) as Golden);

async function replay(frames: EnvelopeFrame[]): Promise<PoseidonUIMessage> {
  const t = createFrameTranslator();
  const chunks: UIMessageChunk[] = [];
  for (const frame of frames) chunks.push(...t.translate(frame));
  chunks.push(...t.end());
  const stream = new ReadableStream<UIMessageChunk>({
    start(c) { for (const ch of chunks) c.enqueue(ch); c.close(); },
  });
  let last: PoseidonUIMessage | undefined;
  for await (const m of readUIMessageStream<PoseidonUIMessage>({ stream })) last = m;
  return last!;
}

describe("contracts/turns replay through the translator and adapter", () => {
  expect(goldens.length).toBeGreaterThanOrEqual(5);
  it.each(goldens.map((g) => [g.scenario, g] as const))("%s: parts equal what Python persisted", async (_, g) => {
    const message = await replay(g.frames);
    expect(toMessageParts(message)).toEqual(g.parts);
    expect(message.metadata?.status).toBe(g.terminal.event === "done" ? "ok" : "error");
    expect(message.metadata?.turn_id).toBe(g.frames[0].data.turn_id);
  });
});
```

- [ ] **Step 3: Run to verify failure**

`cd web && npx vitest run src/lib/turn-stream.test.ts src/lib/message-parts.test.ts src/lib/turn-contracts.test.ts`. Expected: cannot resolve `./turn-stream` / `./message-parts`. Right failure.

- [ ] **Step 4: Implement `turn-stream.ts`**

```ts
import type { UIMessageChunk } from "ai";

export type EnvelopeFrame = {
  event: string;
  data: { turn_id: string; message_id: string; event_seq: number } & Record<string, unknown>;
};

export const TEXT_PART_ID = "text-1";
const ENVELOPE_KEYS = new Set(["turn_id", "message_id", "event_seq"]);

/** Port of frontend/src/api/sse.ts parseSseChunk; `id:` lines are ignored, the envelope carries event_seq. */
export function parseSseChunk(buffer: string): { frames: EnvelopeFrame[]; rest: string } {
  const frames: EnvelopeFrame[] = [];
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
    if (!event || dataLines.length === 0) continue;
    try {
      frames.push({ event, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      continue; // malformed frame: skip, never kill the stream
    }
  }
  return { frames, rest };
}

function payloadOf(frame: EnvelopeFrame): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(frame.data)) if (!ENVELOPE_KEYS.has(k)) out[k] = v;
  return out;
}

/**
 * Spec section 6, frame by frame. Pure: no I/O, no clock. The only state is
 * whether the streamed text part is open and whether a terminal frame was
 * seen, so `end()` can close a cut-off stream honestly.
 */
export function createFrameTranslator() {
  let textOpen = false;
  let finished = false;
  let turnId: string | undefined;

  const closeText = (): UIMessageChunk[] => {
    if (!textOpen) return [];
    textOpen = false;
    return [{ type: "text-end", id: TEXT_PART_ID }];
  };

  return {
    translate(frame: EnvelopeFrame): UIMessageChunk[] {
      turnId ??= frame.data.turn_id;
      const p = payloadOf(frame);
      switch (frame.event) {
        case "accepted":
          return [{ type: "start", messageId: frame.data.message_id, messageMetadata: { turn_id: frame.data.turn_id, turn_index: p.turn_index } }];
        case "phase":
          return [{ type: "data-phase", id: String(p.phase), data: p, transient: true }];
        case "tool":
          return [{ type: "data-tool_event", id: `tool-${p.tool_seq}`, data: p }];
        case "part":
          return [{ type: `data-${p.kind}`, data: p.payload } as UIMessageChunk];
        case "token": {
          const out: UIMessageChunk[] = [];
          if (!textOpen) { textOpen = true; out.push({ type: "text-start", id: TEXT_PART_ID }); }
          out.push({ type: "text-delta", id: TEXT_PART_ID, delta: String(p.text) });
          return out;
        }
        case "done":
          finished = true;
          return [...closeText(), { type: "finish", messageMetadata: { turn_id: turnId, status: "ok", usage: p.usage, title: p.title } }];
        case "error":
          finished = true;
          return [...closeText(), { type: "data-error", data: p } as UIMessageChunk, { type: "finish", messageMetadata: { turn_id: turnId, status: "error" } }];
        default:
          return []; // unknown frame kinds are ignored, never fatal
      }
    },
    end(): UIMessageChunk[] {
      if (finished) return [];
      finished = true;
      return [...closeText(), { type: "error", errorText: "turn stream ended without a done or error frame" }];
    },
  };
}
```

If `ai`'s `UIMessageChunk` type rejects `messageMetadata` on `start`/`finish` or the `transient` flag by name, check the installed `.d.ts` (`node_modules/ai/dist/index.d.ts`) for the exact field names and adjust; the tests fix the semantics, the type names come from the installed package.

Note the `done` test expects `messageMetadata` without `turn_index`; the translator emits `turn_id` on both `start` and `finish` so that whether the SDK merges or replaces metadata, the final message carries `turn_id` and `status` (Global Constraints: merge semantics unverified).

- [ ] **Step 5: Implement `message-parts.ts`**

```ts
import type { UIMessage } from "ai";

export type MessagePart = { kind: string; payload: unknown };
export type TurnMetadata = { turn_id?: string; turn_index?: number; status?: "ok" | "error"; usage?: unknown; title?: string | null };
export type PoseidonUIMessage = UIMessage<TurnMetadata, Record<string, unknown>>;

const SKIPPED = new Set(["step-start", "data-phase", "reasoning", "source-url", "source-document", "file"]);

/**
 * UI message -> Poseidon's persisted shape. Mirrors backend/poseidon/api/
 * live_chat.py's fold (`_record_transcript_frame` + TurnTranscriptBuffer):
 * a `part` is appended verbatim, a `tool` upserts by tool_seq (the SDK's id
 * reconciliation already did that), a `token` appends to the TRAILING text
 * part if there is one. Only a streamed `text` UI part merges backward;
 * a `data-text` part never does.
 */
export function toMessageParts(message: PoseidonUIMessage): MessagePart[] {
  const out: MessagePart[] = [];
  for (const part of message.parts) {
    if (SKIPPED.has(part.type)) continue;
    if (part.type === "text") {
      const prev = out[out.length - 1];
      if (prev && prev.kind === "text") {
        const md = (prev.payload as { markdown: string }).markdown + part.text;
        out[out.length - 1] = { kind: "text", payload: { markdown: md } };
      } else {
        out.push({ kind: "text", payload: { markdown: part.text } });
      }
      continue;
    }
    if (part.type.startsWith("data-")) {
      out.push({ kind: part.type.slice(5), payload: (part as { data: unknown }).data });
    }
  }
  return out;
}

/** Persisted history -> UI message parts, the inverse for page load. */
export function fromMessageParts(parts: MessagePart[]): PoseidonUIMessage["parts"] {
  return parts.map((p) => {
    if (p.kind === "text") {
      return { type: "text", text: (p.payload as { markdown: string }).markdown, state: "done" as const };
    }
    if (p.kind === "tool_event") {
      const seq = (p.payload as { tool_seq: number }).tool_seq;
      return { type: "data-tool_event", id: `tool-${seq}`, data: p.payload } as PoseidonUIMessage["parts"][number];
    }
    return { type: `data-${p.kind}`, data: p.payload } as PoseidonUIMessage["parts"][number];
  });
}
```

- [ ] **Step 6: Run to verify pass**

All three files green. The contract test is the load-bearing one: if any scenario's parts differ from Python's, the translator or adapter is wrong (or Python's fold does something this plan did not predict — report the exact diff, do not "fix" the golden file). Then `npm test` and `npx tsc --noEmit`.

- [ ] **Step 7: Commit**

```bash
git add web/package.json web/package-lock.json web/src/lib/turn-stream.ts web/src/lib/turn-stream.test.ts web/src/lib/message-parts.ts web/src/lib/message-parts.test.ts web/src/lib/turn-contracts.test.ts
git commit -m "feat(web): envelope-to-AI-SDK translator and parts adapter, pinned by the contracts/turns corpus"
```

---

### Task 12: Web renderer registry

**Files:**
- Modify: `web/package.json` (add `react-markdown@10.1.0`; dev: `jsdom`, `@testing-library/react`)
- Create: `web/vitest.config.ts`, `web/src/ui/message-parts/registry.tsx`, `TextPart.tsx`, `ChipsPart.tsx`, `ToolEventPart.tsx`, `ErrorPart.tsx`, `TablePart.tsx`, `ProofPart.tsx`, `MetricGridPart.tsx`, `ArtifactPart.tsx`, `PhaseSectionPart.tsx`, `FallbackPart.tsx`
- Test: `web/src/ui/message-parts/registry.test.tsx`

**Interfaces:**
- Consumes: `MessagePart` from `web/src/lib/message-parts.ts`; the Vite originals under `frontend/src/ui/message-parts/` as the behavioural reference (read each; port, do not import — the two apps are on different React majors).
- Produces: `PartRenderer({ part, onChipSelect?, disabled? })` and `PartProps`. Payload types are copied from `frontend/src/api/types.ts` (`TextPayload`, `ChipsPayload`, `ToolEventPayload`, `ErrorPayload`, `TablePayload`, `ProofPayload`, `MetricGridPayload`, `ArtifactPayload`, `PhaseSectionPayload`) into `registry.tsx`.

- [ ] **Step 1: Install and configure**

`cd web && npm install react-markdown@10.1.0 && npm install -D jsdom @testing-library/react`. Create `web/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node", // db and lib tests; component tests opt into jsdom per file
  },
});
```

(If vitest 5 rejects a config without a Vite plugin for TSX, add `@vitejs/plugin-react` to devDependencies and `plugins: [react()]`; report which was needed.)

- [ ] **Step 2: Write the failing test**

`web/src/ui/message-parts/registry.test.tsx`, first line `// @vitest-environment jsdom`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PartRenderer } from "./registry";

afterEach(cleanup);

describe("PartRenderer", () => {
  it("renders markdown text", () => {
    render(<PartRenderer part={{ kind: "text", payload: { markdown: "**bold** words" } }} />);
    expect(screen.getByText("bold").tagName).toBe("STRONG");
  });

  it("chips send send_text, falling back to the label, and honour disabled", () => {
    const onChipSelect = vi.fn();
    render(
      <PartRenderer
        part={{ kind: "chips", payload: { options: [{ id: "a", label: "Existing customer", send_text: "start an existing-customer brief" }, { id: "b", label: "Plain" }] } }}
        onChipSelect={onChipSelect}
      />,
    );
    fireEvent.click(screen.getByText("Existing customer"));
    fireEvent.click(screen.getByText("Plain"));
    expect(onChipSelect.mock.calls).toEqual([["a", "start an existing-customer brief"], ["b", "Plain"]]);
    cleanup();
    render(<PartRenderer part={{ kind: "chips", payload: { options: [{ id: "a", label: "X" }] } }} onChipSelect={onChipSelect} disabled />);
    expect((screen.getByText("X") as HTMLButtonElement).disabled).toBe(true);
  });

  it("tool events show the label and status", () => {
    render(<PartRenderer part={{ kind: "tool_event", payload: { tool_seq: 1, tool: "data_qa.metric_query", server: null, status: "done", label: "Metric query" } }} />);
    expect(screen.getByText(/Metric query/)).toBeTruthy();
  });

  it("tables render columns and rows", () => {
    render(<PartRenderer part={{ kind: "table", payload: { columns: ["Customer", "GP"], rows: [["Acme", 12.5]] } }} />);
    expect(screen.getByText("Acme")).toBeTruthy();
    expect(screen.getByText("12.5")).toBeTruthy();
  });

  it("metric grids render friendly names and a dash for null", () => {
    render(<PartRenderer part={{ kind: "metric_grid", payload: { periods: { a: { start: "2025-01-01", end: "2025-03-31" }, b: { start: "2025-04-01", end: "2025-06-30" } }, metrics: [{ name: "GP", friendly: "Gross profit", a: 1000, b: null, unit: "USD" }] } }} />);
    expect(screen.getByText("Gross profit")).toBeTruthy();
    expect(screen.getByText("1,000")).toBeTruthy();
  });

  it("artifacts render a plain download anchor", () => {
    render(<PartRenderer part={{ kind: "artifact", payload: { name: "brief.pdf", url: "https://example.invalid/b.pdf", mime: "application/pdf" } }} />);
    const a = screen.getByText("brief.pdf").closest("a")!;
    expect(a.getAttribute("href")).toBe("https://example.invalid/b.pdf");
  });

  it("proof is collapsed, phase sections are open, errors show the message, unknown kinds fall back to JSON", () => {
    render(<PartRenderer part={{ kind: "proof", payload: { lines: ["SELECT 1"] } }} />);
    expect((screen.getByText("Proof").closest("details") as HTMLDetailsElement).open).toBe(false);
    cleanup();
    render(<PartRenderer part={{ kind: "phase_section", payload: { title: "Contextualize", markdown: "done" } }} />);
    expect((screen.getByText("Contextualize").closest("details") as HTMLDetailsElement).open).toBe(true);
    cleanup();
    render(<PartRenderer part={{ kind: "error", payload: { code: "x", message: "It broke" } }} />);
    expect(screen.getByText("It broke")).toBeTruthy();
    cleanup();
    render(<PartRenderer part={{ kind: "report", payload: { any: 1 } }} />);
    expect(screen.getByText(/"any": 1/)).toBeTruthy();
  });
});
```

- [ ] **Step 3: Run to verify failure** — cannot resolve `./registry`. Right failure.

- [ ] **Step 4: Port the eleven files**

`registry.tsx`:

```tsx
import type { ComponentType } from "react";
import type { MessagePart } from "../../lib/message-parts";
import { ArtifactPart } from "./ArtifactPart";
import { ChipsPart } from "./ChipsPart";
import { ErrorPart } from "./ErrorPart";
import { FallbackPart } from "./FallbackPart";
import { MetricGridPart } from "./MetricGridPart";
import { PhaseSectionPart } from "./PhaseSectionPart";
import { ProofPart } from "./ProofPart";
import { TablePart } from "./TablePart";
import { TextPart } from "./TextPart";
import { ToolEventPart } from "./ToolEventPart";

export interface PartProps {
  part: MessagePart;
  onChipSelect?: (id: string, sendText: string) => void;
  disabled?: boolean;
}

// The nine registered kinds plus FallbackPart = doc 01 section 4's ten.
const registry: Record<string, ComponentType<PartProps>> = {
  text: TextPart, chips: ChipsPart, tool_event: ToolEventPart, error: ErrorPart, table: TablePart,
  proof: ProofPart, metric_grid: MetricGridPart, artifact: ArtifactPart, phase_section: PhaseSectionPart,
};

export function PartRenderer(props: PartProps) {
  const Renderer = registry[props.part.kind] ?? FallbackPart;
  return <Renderer {...props} />;
}
```

Each component: read its Vite original and port it with the same behaviour and Tailwind classes instead of the theme classes. Rules that the tests pin: `TextPart` and `PhaseSectionPart` use `react-markdown` (`import Markdown from "react-markdown"`); `ChipsPart` renders `<button type="button" disabled={disabled} onClick={() => onChipSelect?.(o.id, o.send_text ?? o.label)}>`; `ProofPart` is `<details>` closed with a `<summary>Proof</summary>`; `PhaseSectionPart` is `<details open>` with the title as summary; `MetricGridPart`'s `formatValue` renders `null` as `—` and numbers via `toLocaleString("en-US")`; `ArtifactPart` is `<a href={url} download={name}>{name}</a>` plus a mime badge; `ToolEventPart` renders `label` and a glyph per status (`…` start, `✓` done, `✕` error); `ErrorPart` shows `message` and optional `hint`; `FallbackPart` is `<details><summary>{kind}</summary><pre>{JSON.stringify(payload, null, 2)}</pre></details>`. Copy the payload interfaces from `frontend/src/api/types.ts` into `registry.tsx` and export them.

- [ ] **Step 5: Run to verify pass; `npm test`; `npx tsc --noEmit`; commit**

```bash
git add web/package.json web/package-lock.json web/vitest.config.ts web/src/ui/message-parts
git commit -m "feat(web): message-part renderer registry ported from the Vite app (nine kinds plus fallback)"
```

---

### Task 13: Conversation creation in web, with opener parity

**Files:**
- Modify: `web/package.json` (add `uuid@^11`), `web/src/lib/conversations.ts`, `web/src/app/page.tsx`
- Create: `web/src/lib/opener.ts`
- Test: `web/src/lib/opener.test.ts`, `web/src/lib/conversations.test.ts` (extend, live-gated)

**Interfaces:**
- Consumes: `contracts/turns/opener.json` (Task 10); `withUser`; `requireSales` (Task 5).
- Produces: `OPENER_PARTS: MessagePart[]`; `createConversation(sub: string): Promise<string>` (returns the new id); a "New conversation" form on `/`.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/opener.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { OPENER_PARTS } from "./opener";

describe("OPENER_PARTS", () => {
  it("equals Python's _build_opener_parts() byte for byte (contracts/turns/opener.json)", () => {
    const golden = JSON.parse(readFileSync(join(__dirname, "..", "..", "..", "contracts", "turns", "opener.json"), "utf8"));
    expect(OPENER_PARTS).toEqual(golden.parts);
  });
});
```

Extend `web/src/lib/conversations.test.ts` inside its live block:

```ts
  it("createConversation inserts a conversation and its opener under RLS, newest first", async () => {
    const id = await createConversation("dev|alice");
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    const loaded = await loadConversation("dev|alice", id);
    expect(loaded?.conversation.title).toBe("New chat");
    expect(loaded?.messages.map((m) => m.role)).toEqual(["assistant"]);
    expect(loaded?.messages[0].parts).toEqual(OPENER_PARTS);
    expect(await loadConversation("dev|bob", id)).toBeNull();
    expect((await listConversations("dev|alice"))[0].id).toBe(id);
  });
```

(add the cleanup of that row to the file's `afterAll` the way its other inserts are cleaned.)

- [ ] **Step 2: Run to verify failure** — `./opener` unresolvable; `createConversation` not exported.

- [ ] **Step 3: Implement**

`cd web && npm install uuid@^11`. `web/src/lib/opener.ts`:

```ts
import type { MessagePart } from "./message-parts";

/**
 * Python's `_build_opener_parts()` (backend/poseidon/core/chat/history.py),
 * transcribed. The D19 entry phrases are pinned on the Python side
 * (orchestrator.py ENTRY_PHRASE_EXISTING / ENTRY_PHRASE_PROSPECT) and by
 * contracts/turns/opener.json, which opener.test.ts compares against.
 */
export const OPENER_PARTS: MessagePart[] = [
  { kind: "text", payload: { markdown: "Ask about your data, or pick a flow:" } },
  {
    kind: "chips",
    payload: {
      options: [
        { id: "existing_customer", label: "Existing customer", send_text: "start an existing-customer brief" },
        { id: "new_prospect", label: "New customer prospect", send_text: "start a new-prospect brief" },
      ],
    },
  },
];
```

`web/src/lib/conversations.ts`:

```ts
import { v7 as uuidv7 } from "uuid";
import { OPENER_PARTS } from "./opener";

/** Mirrors HistoryStore.create_conversation (history.py:397): uuid7 ids, title "New chat", mode default, opener assistant message, one transaction. */
export async function createConversation(sub: string): Promise<string> {
  const id = uuidv7();
  const openerId = uuidv7();
  await withUser(sub, async (tx) => {
    await tx.insert(conversations).values({ id, userSub: sub, title: "New chat", mode: "default", state: {} });
    await tx.insert(messages).values({ id: openerId, conversationId: id, userSub: sub, role: "assistant", parts: OPENER_PARTS, turnId: null });
  });
  return id;
}
```

`web/src/app/page.tsx` — add a server action and form:

```tsx
import { redirect } from "next/navigation";
import { createConversation, listConversations } from "../lib/conversations";
import { requireSales } from "../lib/request-identity";

async function newConversation() {
  "use server";
  const { sub } = requireSales(await headers());
  const id = await createConversation(sub);
  redirect(`/c/${id}`);
}
...
      <form action={newConversation} className="mt-4">
        <button type="submit" className="rounded border px-3 py-1">New conversation</button>
      </form>
```

- [ ] **Step 4: Verify pass (offline + live), `npm test`, tsc, commit**

```bash
git add web/package.json web/package-lock.json web/src/lib/opener.ts web/src/lib/opener.test.ts web/src/lib/conversations.ts web/src/lib/conversations.test.ts web/src/app/page.tsx
git commit -m "feat(web): create a conversation with Python's opener, pinned against contracts/turns/opener.json"
```

---

### Task 14: The chat route handler and the chat page

**Files:**
- Create: `web/src/lib/turns.ts`, `web/src/app/api/chat/[cid]/route.ts`, `web/src/app/c/[id]/Chat.tsx`
- Modify: `web/src/app/c/[id]/page.tsx`, `web/README.md` (env table: `POSEIDON_INTERNAL_TOKEN`)
- Test: `web/src/lib/turns.test.ts`, `web/src/app/api/chat/[cid]/route.test.ts`

**Interfaces:**
- Consumes: `internalHeaders`, `analyticsOrigin` (Task 2); `createFrameTranslator`, `parseSseChunk` (Task 11); `toMessageParts`, `fromMessageParts`, `PoseidonUIMessage` (Task 11); `PartRenderer` (Task 12); `requireSales` (Task 5); `ai`'s `createUIMessageStream`, `createUIMessageStreamResponse`; `@ai-sdk/react`'s `useChat`; `ai`'s `DefaultChatTransport`.
- Produces: `streamTurn(cid: string, body: { text: string; client_turn_key: string | null }, identity: { sub: string; roles: string[] }, signal?: AbortSignal): Promise<Response>`; `POST /api/chat/[cid]`; the `<Chat>` client component.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/turns.test.ts` (same mocking style as `skills.test.ts`, with `POSEIDON_INTERNAL_TOKEN` stubbed):

```ts
it("posts identity, text and client_turn_key to the internal turns route with the token", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  await streamTurn("c1", { text: "hi", client_turn_key: "k" }, { sub: "dev|local", roles: ["Poseidon:Sales"] });
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://localhost:8000/internal/v1/conversations/c1/turns");
  expect(JSON.parse(init.body)).toEqual({ identity: { sub: "dev|local", roles: ["Poseidon:Sales"] }, text: "hi", client_turn_key: "k" });
  expect(init.headers["x-poseidon-internal-token"]).toBe("test-internal-token");
});
```

`web/src/app/api/chat/[cid]/route.test.ts`:

```ts
import { readUIMessageStream } from "ai";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toMessageParts, type PoseidonUIMessage } from "../../../../lib/message-parts";
import { POST } from "./route";

const SSE =
  'id: 1\nevent: accepted\ndata: {"turn_id":"t","message_id":"m","event_seq":1,"turn_index":0}\n\n' +
  'id: 2\nevent: part\ndata: {"turn_id":"t","message_id":"m","event_seq":2,"kind":"table","payload":{"columns":["a"],"rows":[[1]]}}\n\n' +
  'id: 3\nevent: token\ndata: {"turn_id":"t","message_id":"m","event_seq":3,"text":"Done."}\n\n' +
  'id: 4\nevent: done\ndata: {"turn_id":"t","message_id":"m","event_seq":4,"usage":{}}\n\n';

function sseResponse(text: string) {
  return new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(text)); c.close(); } }), {
    status: 200, headers: { "content-type": "text/event-stream" },
  });
}

const salesHeaders = { "x-poseidon-sub": "dev|local", "x-poseidon-roles": "Poseidon:Sales", "content-type": "application/json" };
const params = Promise.resolve({ cid: "c1" });

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("POST /api/chat/[cid]", () => {
  it("re-streams Python's frames as a UI message stream whose parts match the persisted shape", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "t");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(SSE)));
    const res = await POST(new Request("http://x/api/chat/c1", { method: "POST", headers: salesHeaders, body: JSON.stringify({ text: "hi", client_turn_key: "k" }) }), { params });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    // The response body is the SSE encoding of UI chunks; decode it back through the SDK's reader.
    let last: PoseidonUIMessage | undefined;
    for await (const m of readUIMessageStream<PoseidonUIMessage>({ stream: uiChunksFromSse(res) })) last = m;
    expect(toMessageParts(last!)).toEqual([{ kind: "table", payload: { columns: ["a"], rows: [[1]] } }, { kind: "text", payload: { markdown: "Done." } }]);
    expect(last!.metadata?.status).toBe("ok");
  });

  it("passes a pre-stream non-2xx from Python through with its body", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "t");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ title: "unknown conversation" }), { status: 404, headers: { "content-type": "application/json" } })));
    const res = await POST(new Request("http://x/api/chat/c1", { method: "POST", headers: salesHeaders, body: JSON.stringify({ text: "hi", client_turn_key: "k" }) }), { params });
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ title: "unknown conversation" });
  });

  it("refuses without the Sales role before calling Python", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(POST(new Request("http://x/api/chat/c1", { method: "POST", headers: { ...salesHeaders, "x-poseidon-roles": "" }, body: "{}" }), { params })).rejects.toThrow(/lacks Poseidon:Sales/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
```

`uiChunksFromSse(res)`: a small helper in the test that reads `res.body`, splits SSE `data:` lines and `JSON.parse`s each into a `UIMessageChunk`, producing a `ReadableStream<UIMessageChunk>` (the SDK's response encoder writes one `data: {json}` line per chunk plus a final `data: [DONE]`; skip that sentinel). If `ai` exports a parser for its own SSE encoding, use it instead and say so.

- [ ] **Step 2: Run to verify failure** — modules unresolvable. Right failure.

- [ ] **Step 3: Implement `turns.ts`**

```ts
import { analyticsOrigin, internalHeaders } from "./skills";

export async function streamTurn(
  cid: string,
  body: { text: string; client_turn_key: string | null },
  identity: { sub: string; roles: string[] },
  signal?: AbortSignal,
): Promise<Response> {
  const headers = internalHeaders(); // throws before any request when the token is unset
  const url = `${analyticsOrigin()}/internal/v1/conversations/${encodeURIComponent(cid)}/turns`;
  return fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({ identity: { sub: identity.sub, roles: identity.roles }, text: body.text, client_turn_key: body.client_turn_key }),
    signal,
  });
}
```

- [ ] **Step 4: Implement the route handler**

`web/src/app/api/chat/[cid]/route.ts`:

```ts
import { createUIMessageStream, createUIMessageStreamResponse, type UIMessageChunk } from "ai";
import { requireSales } from "../../../../lib/request-identity";
import { createFrameTranslator, parseSseChunk } from "../../../../lib/turn-stream";
import { streamTurn } from "../../../../lib/turns";

export const dynamic = "force-dynamic";
const IDLE_MS = 120_000; // spec section 10: no overall timeout, an idle timeout between frames

export async function POST(request: Request, { params }: { params: Promise<{ cid: string }> }) {
  const { cid } = await params;
  const identity = requireSales(request.headers);
  const { text, client_turn_key = null } = (await request.json()) as { text: string; client_turn_key?: string | null };

  const controller = new AbortController();
  let idle = setTimeout(() => controller.abort(), IDLE_MS);
  const touch = () => { clearTimeout(idle); idle = setTimeout(() => controller.abort(), IDLE_MS); };

  const upstream = await streamTurn(cid, { text, client_turn_key }, identity, controller.signal);
  if (!upstream.ok || !upstream.body) {
    clearTimeout(idle);
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  }

  const body = upstream.body;
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      const translator = createFrameTranslator();
      const reader = body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          touch();
          buffer += decoder.decode(value, { stream: true });
          const { frames, rest } = parseSseChunk(buffer);
          buffer = rest;
          for (const frame of frames) for (const chunk of translator.translate(frame)) writer.write(chunk);
        }
        for (const chunk of translator.end()) writer.write(chunk);
      } catch (err) {
        writer.write({ type: "error", errorText: `turn stream interrupted: ${(err as Error).message}` } as UIMessageChunk);
      } finally {
        clearTimeout(idle);
      }
    },
  });
  return createUIMessageStreamResponse({ stream });
}
```

- [ ] **Step 5: The page and the client component**

`web/src/app/c/[id]/page.tsx` — keep the server-side load, hand the client component the history:

```tsx
import { Chat } from "./Chat";
import { fromMessageParts, type PoseidonUIMessage } from "../../../lib/message-parts";
...
  const initial: PoseidonUIMessage[] = loaded.messages.map((m) => ({
    id: m.id,
    role: m.role === "user" ? "user" : "assistant",
    parts: fromMessageParts(m.parts as { kind: string; payload: unknown }[]),
  }));
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-xl font-semibold">{loaded.conversation.title ?? "(untitled)"}</h1>
      <Chat conversationId={id} initialMessages={initial} />
    </main>
  );
```

`web/src/app/c/[id]/Chat.tsx`:

```tsx
"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useMemo, useState } from "react";
import { toMessageParts, type PoseidonUIMessage } from "../../../lib/message-parts";
import { PartRenderer } from "../../../ui/message-parts/registry";

/** client_turn_key per user message, reused if the SDK retries the same message (Python dedups on it). */
const turnKeys = new Map<string, string>();

export function Chat({ conversationId, initialMessages }: { conversationId: string; initialMessages: PoseidonUIMessage[] }) {
  const [draft, setDraft] = useState("");
  const transport = useMemo(
    () =>
      new DefaultChatTransport<PoseidonUIMessage>({
        api: `/api/chat/${conversationId}`,
        prepareSendMessagesRequest: ({ messages }) => {
          const last = messages[messages.length - 1];
          const text = last.parts.filter((p) => p.type === "text").map((p) => (p as { text: string }).text).join("");
          if (!turnKeys.has(last.id)) turnKeys.set(last.id, crypto.randomUUID());
          return { body: { text, client_turn_key: turnKeys.get(last.id) } };
        },
      }),
    [conversationId],
  );
  const { messages, sendMessage, status, error } = useChat<PoseidonUIMessage>({ id: conversationId, messages: initialMessages, transport });
  const busy = status !== "ready";

  function send(text: string) {
    if (busy || text.trim() === "") return;
    setDraft("");
    void sendMessage({ text });
  }

  return (
    <div className="mt-4 space-y-4">
      <ol className="space-y-4">
        {messages.map((m) => (
          <li key={m.id} className={m.role === "user" ? "text-right" : ""}>
            <span className="text-xs uppercase opacity-60">{m.role}</span>
            {m.role === "user" ? (
              <p>{m.parts.filter((p) => p.type === "text").map((p) => (p as { text: string }).text).join("")}</p>
            ) : (
              toMessageParts(m).map((part, i) => (
                <PartRenderer key={i} part={part} disabled={busy} onChipSelect={(_id, sendText) => send(sendText)} />
              ))
            )}
          </li>
        ))}
      </ol>
      {error ? <p className="text-red-600">{error.message}</p> : null}
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
        <input className="flex-1 rounded border px-2 py-1" aria-label="Message Poseidon" placeholder="Message Poseidon…" value={draft} disabled={busy} onChange={(e) => setDraft(e.target.value)} />
        <button type="submit" className="rounded border px-3 py-1" disabled={busy || draft.trim() === ""}>Send</button>
      </form>
    </div>
  );
}
```

If `useChat`'s option or return names in the installed `@ai-sdk/react@3.0.289` differ (`status` values `submitted | streaming | ready | error`), follow the installed `.d.ts` and report.

- [ ] **Step 6: README**

Add to `web/README.md`'s env table: `| \`POSEIDON_INTERNAL_TOKEN\` | none (required) | Shared secret for the Python internal contract; must equal the backend's. Compose uses \`dev-internal-token\`. |`. Add a "Running with the backend" paragraph: start compose, then `$env:POSEIDON_INTERNAL_TOKEN="dev-internal-token"; $env:DRIZZLE_DATABASE_URL="..."; npm run dev`. Add the 403 check: `curl -H "Sf-Context-Current-User: bob" http://localhost:3000/` against a dev server started with `IDENTITY_MODE=spcs_ingress DEPLOY_MODE=spcs SPCS_SALES_USERS=alice` returns 403.

- [ ] **Step 7: Verify pass; `npm test`; tsc; then the live walk**

With compose up (5434 override) and the Next dev server started as the README says: create a conversation on `/`, send `top customers by gross profit in Singapore`, watch the tool line, table, proof and text arrive; reload and see the same parts; click an opener chip and see the brief flow start. Confirm in the Python log that the turn's `X-Trace-Id` equals the one Next sent (log line `trace_id`). Confirm `/api/chat/<id>` is served by Next, not rewritten: the Python access log shows only `/internal/v1/...` for that request. Paste evidence in the report.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/turns.ts web/src/lib/turns.test.ts "web/src/app/api/chat/[cid]/route.ts" "web/src/app/api/chat/[cid]/route.test.ts" "web/src/app/c/[id]/Chat.tsx" "web/src/app/c/[id]/page.tsx" web/README.md
git commit -m "feat(web): chat route handler re-streams Python turns through AI SDK; chat page with composer and renderers"
```

---

### Task 15: Documents, decision log, spec edits

**Files:**
- Modify: `docs/superpowers/specs/2026-09-07-nextjs-migration-design.md` (§3 two rows, §7 route spelling), `docs/architecture/00-overview.md` (decision log: D57, D58, D59), `CLAUDE.md` (Frozen surfaces: the corpus; commands: contracts regenerate, web migrate), `web/README.md` (final pass)

No code. One commit.

- [ ] **Step 1: Migration spec**

§3: the "Turn orchestration" row's target owner becomes `**Python** (Phase 16, D57); Next.js + AI SDK deferred` with note "Bridge: Next.js re-streams Python's turn; the `contracts/turns` corpus is the entry point for a later port." Same for "Model providers, agent loop". "Chat streaming" row: `**Next.js** + AI SDK 6 (transport and client); envelope produced by Python`. §7: `POST /internal/v1/skills/{skill_id}:dispatch` → `/dispatch` (P16-5) and add the turns route line: `POST /internal/v1/conversations/{cid}/turns — turn text + identity in, the SSE envelope out (Phase 16).` Add under non-negotiables: "Authenticated by `POSEIDON_INTERNAL_TOKEN` in `X-Poseidon-Internal-Token` (D59)."

- [ ] **Step 2: Decision log rows** (append after D56 in `docs/architecture/00-overview.md`, same table format):

| D57 | Phase 16 keeps turn orchestration in Python behind the internal contract; Next.js owns transport, client and rendering (bridge). Revises migration spec §3's orchestration and agent-loop rows; the `contracts/turns` corpus is the entry point for a later port. AI SDK pinned to the 6.x line (`ai@6.0.286`). | revises M1's "AI SDK 6" reading and spec §3 |
| D58 | Drizzle is the migration authority for application tables (Q3 applied): `web/drizzle/0000_roles`, `0001_baseline`, `0002_grants_force_rls` re-derived from Alembic 0003–0010. Alembic no longer runs at boot; 0001–0010 are frozen history; the `synthetic` schema (0002) is created by the seed path. Tripwires: `web/src/db/migrations.test.ts` and the pg suite against a Drizzle-only database. | — |
| D59 | The internal contract is authenticated by a shared secret (`POSEIDON_INTERNAL_TOKEN`, header `X-Poseidon-Internal-Token`, constant-time compare, 503 when unset) and hidden from OpenAPI. The web tier requires `Poseidon:Sales` in the proxy (403) and in every page/handler. | closes R25, P16-4 |

- [ ] **Step 3: CLAUDE.md**

Frozen surfaces: add "**The `contracts/turns` corpus.** Golden frame/part sequences pinned on both sides; a change lands only with `POSEIDON_UPDATE_CONTRACTS=1` regeneration in the same commit and a reviewer reading the diff." Commands: add the regenerate line and `cd web && npx drizzle-kit migrate`. Update the web suite count line with the real numbers from Task 14's run.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-07-nextjs-migration-design.md docs/architecture/00-overview.md CLAUDE.md web/README.md
git commit -m "docs(phase-16): decision log D57-D59, migration spec revisions, commands and frozen-surface updates"
```

---

## Phase gate (spec §12)

**Automated, all green at HEAD:** Python offline suite (baseline 1693 + this plan's new tests), `ruff check .`, the pg suite against a Drizzle-only database (Task 8), `cd web && npm test` offline and with `DRIZZLE_DATABASE_URL`, `npx tsc --noEmit`, and the contract corpus test on both sides.

**What Carlos clicks** (written into the morning-review file with exact commands):
1. `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml down -v` then `up -d`. Watch `migrate` complete, then `backend` seed and serve. No Alembic anywhere in the logs.
2. Start the Next dev server per `web/README.md`. Open `http://localhost:3000`, click **New conversation**.
3. Send `top customers by gross profit in Singapore`. A tool line, a table, a proof block and a sentence stream in.
4. Send an ambiguous customer name and click a chip.
5. Click **Existing customer** on a new conversation, give a customer, watch phase sections and tool lines.
6. Reload: the same parts, from history.
7. Open `http://localhost:5173` and send one turn there.
8. The 403 check from the README.

## Out of scope (spec §13), carried forward

Reconnect-on-drop reconcile; feedback, settings, delete, skills picker in Next; the orchestration port; reports; the email procedure; Cortex/SPCS; visual polish; AI SDK 7 upgrade (a later small task: re-verify §6's chunk names against the 7.x docs).

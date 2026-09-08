# Phase 15 — Migration Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a Next.js 16 application beside the existing Vite app that logs in an existing user, reads their existing conversations from the existing database under row-level security, and dispatches one real skill to the Python service — proving every seam of the migration without adding a single feature.

**Architecture:** The Next.js app lives in a new top-level `web/` directory and runs on port 3000 while the Vite app keeps running on 5173 (coexistence, decision Q5). Identity is resolved in Next.js's `proxy.ts` and must produce provider-prefixed subject strings byte-identical to Python's, or existing rows stop matching. Drizzle reads the live schema by introspection in this phase — it does **not** yet take over migrations, because migrations 0009/0010 encode Postgres grants and roles that introspection cannot capture. Python keeps every skill, and Next.js reaches it over a new internal HTTP contract.

**Tech Stack:** Next.js 16 (App Router), TypeScript, Tailwind CSS, Drizzle ORM + drizzle-kit, `postgres` driver, Vitest. Python side: FastAPI (existing), pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-nextjs-migration-design.md` (M1–M5, §3 seam map, §6 identity, §7 internal contract, §8 schema authority)

## Global Constraints

- **Node 24.14.1, npm 11.13.0** — verified present on this machine. Next.js 16 requires Node 20+.
- **Next.js 16 renamed middleware.** The file is `proxy.ts` and it exports `export function proxy(request)`. `middleware.ts` / `export function middleware` is the Next 15 spelling and will silently not run. Verified against current Next.js docs 2026-09-07.
- **`headers()` from `next/headers` is async** — `const h = await headers()`. Not awaiting it yields a Promise, not a header list.
- **Subject strings are load-bearing and must not change.** `dev|local` (disabled mode), `sf|<snowflake-username>` (spcs_ingress), `auth0|<id>` (auth0, not wired this phase). Every persisted row and every RLS policy keys on these. A changed sub silently orphans a user's history.
- **No Auth0 (M5).** Production identity is SPCS ingress; local development is the fixed dev user. The `auth0` branch exists but returns 501.
- **DSN format differs between the two stacks.** Compose sets `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@db:5432/poseidon` — that `+psycopg` is SQLAlchemy syntax and Drizzle will reject it. Drizzle needs `postgresql://poseidon:poseidon@localhost:5432/poseidon`.
- **The Vite app must keep working.** Every gate in this phase includes "and `npm run dev` in `frontend/` still serves the old app." Coexistence is the point.
- **Drizzle does not own migrations yet.** Alembic stays the authority through this phase. Handover happens only after grants and roles are ported deliberately.
- **Always use `.venv/Scripts/python.exe`, never a bare `python`.** Verified 2026-09-08: a bare `python` resolves to a *different* global 3.14.4 that has `fastapi` and `pytest 9.0.3` but **no `sqlalchemy`**, so it imports partway and dies with a confusing `ModuleNotFoundError`. The venv has pytest 9.1.1.
- **Offline Python test command:** `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live'` — baseline **1679 passed, 13 skipped, 116 deselected**. Never pipe pytest: a pipe masks the exit code and a failing run reports success.
- **Postgres-backed Python tests** use `-m pg` and require the compose `db` service up.

---

## File Structure

| File | Responsibility |
|---|---|
| `web/package.json` | Next app manifest and scripts |
| `web/next.config.ts` | `/api/*` rewrite to the FastAPI backend in development |
| `web/proxy.ts` | Next 16 proxy: resolve identity, attach it to the downstream request |
| `web/src/lib/identity.ts` | Pure sub-resolution logic, unit-testable without a server |
| `web/src/lib/identity.test.ts` | Sub-format parity tests |
| `web/drizzle.config.ts` | drizzle-kit config for introspection |
| `web/src/db/schema.ts` | **Generated** by `drizzle-kit pull` — never hand-edited |
| `web/src/db/client.ts` | RLS-aware connection helper |
| `web/src/db/client.test.ts` | Proves cross-user reads return nothing |
| `web/src/lib/skills.ts` | Client for the internal Python contract |
| `web/src/app/page.tsx` | Conversation list |
| `web/src/app/c/[id]/page.tsx` | One conversation's history |
| `backend/poseidon/api/internal.py` | The internal dispatch route |
| `backend/tests/test_internal_dispatch.py` | Its tests |

---

## Task 1: Next.js app skeleton beside the Vite app

**Files:**
- Create: `web/` (scaffolded), `web/next.config.ts`
- Test: `web/src/lib/config.test.ts`

**Interfaces:**
- Consumes: nothing
- Produces: a running Next app on port 3000; `apiRewrites()` exported from `web/next.config.ts`

- [ ] **Step 1: Scaffold the app**

From the repository root:

```bash
npx create-next-app@latest web --typescript --tailwind --app --src-dir --no-eslint --use-npm --skip-install
cd web && npm install && npm install -D vitest
```

- [ ] **Step 2: Write the failing test**

Create `web/src/lib/config.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { apiRewrites } from "../../next.config";

describe("apiRewrites", () => {
  it("forwards /api/* to the FastAPI backend", async () => {
    const rules = await apiRewrites("http://localhost:8000");
    expect(rules).toEqual([
      { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
    ]);
  });
});
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd web && npx vitest run src/lib/config.test.ts`
Expected: FAIL — `apiRewrites` is not exported from `next.config`.

- [ ] **Step 4: Write `web/next.config.ts`**

```ts
import type { NextConfig } from "next";

/** Exported separately so it can be unit-tested without booting Next. */
export async function apiRewrites(target: string) {
  return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
}

const nextConfig: NextConfig = {
  async rewrites() {
    return apiRewrites(process.env.BACKEND_ORIGIN ?? "http://localhost:8000");
  },
};

export default nextConfig;
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `cd web && npx vitest run src/lib/config.test.ts`
Expected: PASS

- [ ] **Step 6: Prove coexistence by hand**

Run `cd web && npm run dev` — expect a page at `http://localhost:3000`.
In a second terminal run `cd frontend && npm run dev` — expect the existing app at `http://localhost:5173`.
Both must serve at once. If port 3000 is taken, that is the finding to report, not a thing to work around silently.

- [ ] **Step 7: Commit**

```bash
git add web/
git commit -m "feat(web): scaffold the Next.js 16 app beside the Vite app"
```

---

## Task 2: Identity resolution with byte-identical subjects

**Files:**
- Create: `web/src/lib/identity.ts`, `web/src/lib/identity.test.ts`, `web/proxy.ts`
- Reference (do not modify): `backend/poseidon/core/identity.py`, `backend/poseidon/core/identity_spcs.py`

**Interfaces:**
- Consumes: Task 1's app
- Produces: `resolveIdentity(mode, deployMode, headers): Identity | AuthError` and the type `Identity = { sub: string; email: string | null; name: string | null; roles: string[] }`

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/identity.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { resolveIdentity } from "./identity";

const h = (o: Record<string, string>) => new Headers(o);

describe("resolveIdentity", () => {
  it("disabled mode returns the fixed dev identity", () => {
    const id = resolveIdentity("disabled", "local", h({}));
    expect(id).toEqual({
      sub: "dev|local",
      email: "dev@local",
      name: "Dev User",
      roles: ["Poseidon:Sales"],
    });
  });

  it("disabled mode honours the X-Dev-User act-as header", () => {
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "alice" }));
    expect(id).toMatchObject({ sub: "dev|alice" });
  });

  it("disabled mode ignores a malformed act-as header rather than rejecting", () => {
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "bad user!" }));
    expect(id).toMatchObject({ sub: "dev|local" });
  });

  it("spcs_ingress mints an sf| sub from the platform header", () => {
    process.env.SPCS_SALES_USERS = "*";
    const id = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "CARLOS" }));
    expect(id).toMatchObject({ sub: "sf|carlos", roles: ["Poseidon:Sales"] });
  });

  it("spcs_ingress grants Sales only to allow-listed users", () => {
    process.env.SPCS_SALES_USERS = "alice,bob";
    const allowed = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "ALICE" }));
    expect(allowed).toMatchObject({ sub: "sf|alice", roles: ["Poseidon:Sales"] });

    // Authenticated by the platform, but not on the list -> no roles.
    const stranger = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "mallory" }));
    expect(stranger).toMatchObject({ sub: "sf|mallory", roles: [] });
  });

  it("rejects a username over the 64-character cap", () => {
    process.env.SPCS_SALES_USERS = "*";
    expect(() => resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "a".repeat(65) })))
      .toThrow(/missing spcs identity header/i);
  });

  it("rejects a dot, which Python's character class excludes", () => {
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "first.last" }));
    expect(id).toMatchObject({ sub: "dev|local" });
  });

  it("spcs_ingress refuses to trust the header outside spcs deploy mode", () => {
    expect(() => resolveIdentity("spcs_ingress", "local", h({ "sf-context-current-user": "CARLOS" })))
      .toThrow(/deploy mode/i);
  });

  it("spcs_ingress 401s when the header is absent", () => {
    expect(() => resolveIdentity("spcs_ingress", "spcs", h({}))).toThrow(/missing spcs identity header/i);
  });

  it("spcs_ingress 401s identically when the header is present but malformed", () => {
    expect(() => resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "a b!" })))
      .toThrow(/missing spcs identity header/i);
  });

  it("auth0 is not wired in this phase", () => {
    expect(() => resolveIdentity("auth0", "local", h({}))).toThrow(/not wired/i);
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd web && npx vitest run src/lib/identity.test.ts`
Expected: FAIL — module `./identity` not found.

- [ ] **Step 3: Implement `web/src/lib/identity.ts`**

```ts
export type Identity = {
  sub: string;
  email: string | null;
  name: string | null;
  roles: string[];
};

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthError";
  }
}

/** Mirrors backend/poseidon/core/identity.py's fixed context exactly. */
const DEV_IDENTITY: Identity = {
  sub: "dev|local",
  email: "dev@local",
  name: "Dev User",
  roles: ["Poseidon:Sales"],
};

/**
 * Port of Python's `sanitize_username` (core/identity.py) -- the ONE rule this
 * codebase applies to any operator- or platform-supplied username-shaped
 * header. Both modes share it there, so both share it here.
 *
 * Python: `_ACT_AS_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")` applied with
 * `.fullmatch()` to the casefolded value. Note: NO dot, and a 1-64 length cap.
 * `fullmatch` is why "alice!" is rejected wholesale rather than truncated to
 * its matching "alice" prefix.
 */
const SAFE_NAME = /^[a-z0-9_-]{1,64}$/;

/** Returns the sanitised username, or null when it does not match. */
function sanitizeUsername(raw: string): string | null {
  const candidate = raw.toLowerCase();
  return SAFE_NAME.test(candidate) ? candidate : null;
}

export function resolveIdentity(
  mode: "disabled" | "spcs_ingress" | "auth0",
  deployMode: string,
  headers: Headers,
): Identity {
  if (mode === "disabled") {
    const actAs = headers.get("x-dev-user");
    const name = actAs ? sanitizeUsername(actAs) : null;
    if (name) {
      return { ...DEV_IDENTITY, sub: `dev|${name}` };
    }
    // An invalid act-as header is IGNORED, never rejected -- the whole point
    // of this mode is that it never refuses a request.
    return DEV_IDENTITY;
  }

  if (mode === "spcs_ingress") {
    // The header is unsigned and unverifiable. It is trustworthy ONLY because
    // the SPCS platform edge sets it and nothing else can reach the service.
    // Anywhere else, any caller could forge it.
    if (deployMode !== "spcs") {
      throw new AuthError("spcs identity header is not trusted outside spcs deploy mode");
    }
    const raw = headers.get("sf-context-current-user");
    const name = raw === null ? null : sanitizeUsername(raw);
    // A present-but-malformed header raises the SAME error as an absent one:
    // both mean the trusted edge did not deliver what it guarantees. Python
    // has no separate "malformed" bucket here either.
    if (!name) {
      throw new AuthError("missing spcs identity header");
    }
    // Roles are ALLOWLIST-GATED, not granted to everyone the platform
    // authenticates -- mirrors identity_spcs.py:156,
    // `roles = (_SALES_ROLE,) if self._is_allowed(candidate) else ()`.
    // The allowlist is Settings.spcs_sales_users, casefolded, and "*" means
    // everyone. A user the platform authenticated but who is NOT on the list
    // gets an empty role list, and require_sales then 403s them.
    const allowlist = new Set(
      (process.env.SPCS_SALES_USERS ?? "").split(",").map((n) => n.trim().toLowerCase()).filter(Boolean),
    );
    const allowed = allowlist.has("*") || allowlist.has(name);
    return {
      sub: `sf|${name}`,
      email: null,
      name: null,
      roles: allowed ? ["Poseidon:Sales"] : [],
    };
  }

  throw new AuthError("auth0 mode is not wired in this phase (decision M5)");
}
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd web && npx vitest run src/lib/identity.test.ts`
Expected: PASS, 8 tests.

- [ ] **Step 5: Prove sub parity against Python, not just against this test file**

Run from `backend/`:

```bash
cd backend && .venv/Scripts/python.exe -B -c "from poseidon.core.identity import DISABLED_DEFAULT_USER as U; print(U.sub, U.email, U.name, U.roles)"
```

Expected, verified 2026-09-08: `dev|local dev@local Dev User ('Poseidon:Sales',)` — matching
`DEV_IDENTITY` above exactly. The constant is `DISABLED_DEFAULT_USER`
(`backend/poseidon/core/identity.py:130`). Never adjust the Python to fit the TypeScript.

- [ ] **Step 6: Wire `web/proxy.ts`**

Next 16 calls this `proxy`, not `middleware`:

```ts
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { AuthError, resolveIdentity } from "./src/lib/identity";

export function proxy(request: NextRequest) {
  const mode = (process.env.IDENTITY_MODE ?? "disabled") as
    | "disabled"
    | "spcs_ingress"
    | "auth0";
  const deployMode = process.env.DEPLOY_MODE ?? "local";

  let identity;
  try {
    identity = resolveIdentity(mode, deployMode, request.headers);
  } catch (err) {
    if (err instanceof AuthError) {
      return NextResponse.json(
        { type: "about:blank", title: "unauthorized", detail: err.message },
        { status: 401 },
      );
    }
    throw err;
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-poseidon-sub", identity.sub);
  requestHeaders.set("x-poseidon-roles", identity.roles.join(","));
  return NextResponse.next({ request: { headers: requestHeaders } });
}
```

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/identity.ts web/src/lib/identity.test.ts web/proxy.ts
git commit -m "feat(web): resolve identity with subjects byte-identical to Python's"
```

---

## Task 3: Pull the live schema into Drizzle

**Files:**
- Create: `web/drizzle.config.ts`, `web/src/db/schema.ts` (generated), `web/src/db/schema.test.ts`

**Interfaces:**
- Consumes: a running compose `db`
- Produces: `web/src/db/schema.ts` exporting Drizzle table objects including `conversations` and `messages`

- [ ] **Step 1: Bring the database up**

```bash
docker compose -f infra/docker-compose.yml up -d db
```

- [ ] **Step 2: Install drizzle**

```bash
cd web && npm install drizzle-orm postgres && npm install -D drizzle-kit
```

- [ ] **Step 3: Write `web/drizzle.config.ts`**

Note the DSN: plain `postgresql://`, **not** compose's `postgresql+psycopg://`.

```ts
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  dbCredentials: {
    url:
      process.env.DRIZZLE_DATABASE_URL ??
      "postgresql://poseidon:poseidon@localhost:5432/poseidon",
  },
  schemaFilter: ["app", "public"],
  tablesFilter: ["*"],
});
```

- [ ] **Step 4: Write the failing test**

Create `web/src/db/schema.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import * as schema from "./schema";

describe("introspected schema", () => {
  it("includes the chat history tables", () => {
    expect(schema).toHaveProperty("conversations");
    expect(schema).toHaveProperty("messages");
  });

  it("keys conversations on the provider-prefixed sub", () => {
    const cols = Object.keys((schema.conversations as any)[Symbol.for("drizzle:Columns")]);
    expect(cols).toContain("userSub");
  });
});
```

- [ ] **Step 5: Run it and watch it fail**

Run: `cd web && npx vitest run src/db/schema.test.ts`
Expected: FAIL — `./schema` does not exist yet.

- [ ] **Step 6: Introspect**

```bash
cd web && npx drizzle-kit pull
```

Expected: `src/db/schema.ts` is written, containing `conversations`, `messages`, `turn_run`, `llm_calls`, `tool_calls`, `message_feedback` and the personalization tables.

- [ ] **Step 7: Run the test and watch it pass**

Run: `cd web && npx vitest run src/db/schema.test.ts`
Expected: PASS

If the column is not named `userSub`, read the generated file and correct the **test** to the real name — the generated schema is the truth here, not this plan.

- [ ] **Step 8: Record what introspection did NOT capture**

Add this comment block at the top of `web/src/db/schema.ts`, above the generated content:

```ts
// GENERATED by `npx drizzle-kit pull`. Do not hand-edit; re-pull instead.
//
// Alembic remains the migration authority for this phase. Introspection
// captures table shapes ONLY. It does NOT capture:
//   - row-level security policies (conversations_owner, messages_owner)
//   - FORCE ROW LEVEL SECURITY
//   - the poseidon_app and poseidon_worker roles and their grants
//     (migrations 0009 and 0010), which exist because RDS has no superuser
// Handing migration authority to Drizzle (decision Q3) requires porting
// those deliberately first. Until then, `drizzle-kit push`/`generate` must
// NOT be run against this database.
```

- [ ] **Step 9: Commit**

```bash
git add web/drizzle.config.ts web/src/db/schema.ts web/src/db/schema.test.ts web/package.json web/package-lock.json
git commit -m "feat(web): introspect the live schema into Drizzle, read-only for now"
```

---

## Task 4: An RLS-aware database client

**Files:**
- Create: `web/src/db/client.ts`, `web/src/db/client.test.ts`

**Interfaces:**
- Consumes: Task 3's `schema.ts`
- Produces: `withUser<T>(sub: string, fn: (tx) => Promise<T>): Promise<T>`

This is the security-critical task. It must fail closed.

- [ ] **Step 1: Write the failing test**

Create `web/src/db/client.test.ts`:

```ts
import { beforeAll, describe, expect, it } from "vitest";
import { conversations } from "./schema";
import { withUser } from "./client";

// Requires the compose `db` service. Skipped when DRIZZLE_DATABASE_URL is unset.
const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("withUser", () => {
  let aliceConversationId: string;

  beforeAll(async () => {
    aliceConversationId = await withUser("dev|alice", async (tx) => {
      const [row] = await tx
        .insert(conversations)
        .values({ id: crypto.randomUUID(), userSub: "dev|alice", title: "alice's" })
        .returning();
      return row.id;
    });
  });

  it("lets a user read their own row", async () => {
    const rows = await withUser("dev|alice", (tx) => tx.select().from(conversations));
    expect(rows.map((r) => r.id)).toContain(aliceConversationId);
  });

  it("returns nothing for another user's row", async () => {
    const rows = await withUser("dev|bob", (tx) => tx.select().from(conversations));
    expect(rows.map((r) => r.id)).not.toContain(aliceConversationId);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd web && DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5432/poseidon npx vitest run src/db/client.test.ts`
Expected: FAIL — `./client` not found.

- [ ] **Step 3: Implement `web/src/db/client.ts`**

```ts
import { drizzle } from "drizzle-orm/postgres-js";
import { sql } from "drizzle-orm";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString =
  process.env.DRIZZLE_DATABASE_URL ??
  "postgresql://poseidon:poseidon@localhost:5432/poseidon";

const client = postgres(connectionString);
const db = drizzle(client, { schema });

/**
 * Port of Python's `rls_transaction` (backend/poseidon/core/db.py:321).
 * Mirrors it statement for statement, in the same order.
 *
 * `set_config(..., true)` -- the trailing `true` is `is_local`, scoping the
 * setting to THIS transaction. Without it the value would leak to whatever
 * request next borrows this pooled connection, which is precisely the bug
 * the parameter exists to prevent. It is deliberately the FIRST statement of
 * the transaction (decision D28), before any role switch.
 *
 * `SET LOCAL ROLE "<role>"` then drops to a non-owner, non-BYPASSRLS role, so
 * a forgotten filter returns zero foreign rows instead of leaking them. Python
 * takes this role from `Settings.database_app_role`, which DEFAULTS to
 * "poseidon_app" and treats an empty string as "no role switch" -- so this
 * mirrors that: `DATABASE_APP_ROLE` env var, same default, empty means skip.
 *
 * The role name is INTERPOLATED, not bound: `SET ROLE` accepts no bind
 * parameter, unlike `set_config`. Python guards that with `_validate_app_role`
 * against `[a-z_][a-z0-9_]{0,62}` before building any SQL; this does the same.
 * Python also double-quotes the identifier, and so does this.
 */
const APP_ROLE_PATTERN = /^[a-z_][a-z0-9_]{0,62}$/;

function appRole(): string | null {
  const raw = process.env.DATABASE_APP_ROLE ?? "poseidon_app";
  if (raw === "") return null; // the ergonomic "unset", same as Python
  if (!APP_ROLE_PATTERN.test(raw)) {
    throw new Error(
      `DATABASE_APP_ROLE=${JSON.stringify(raw)} is not a valid Postgres role identifier ` +
        "(expected [a-z_][a-z0-9_]{0,62}) -- refusing to interpolate it into SQL",
    );
  }
  return raw;
}

export async function withUser<T>(
  sub: string,
  fn: (tx: typeof db) => Promise<T> | T,
): Promise<T> {
  const role = appRole(); // validated BEFORE any connection is opened
  return db.transaction(async (tx) => {
    await tx.execute(sql`SELECT set_config('app.user_sub', ${sub}, true)`);
    if (role !== null) {
      await tx.execute(sql.raw(`SET LOCAL ROLE "${role}"`));
    }
    return await fn(tx as unknown as typeof db);
  });
}
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd web && DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5432/poseidon npx vitest run src/db/client.test.ts`
Expected: PASS, 2 tests.

**If the isolation test passes trivially** — because the compose connection is a superuser and RLS is not being enforced at all — that is a real finding, not a pass. Verify by running the same select **without** `withUser` and confirming it *does* see Alice's row. Python hit exactly this: the compose DSN is a superuser that `FORCE` cannot bind, which is why `SET LOCAL ROLE` exists in this helper. Report it if the behaviour differs.

- [ ] **Step 5: Commit**

```bash
git add web/src/db/client.ts web/src/db/client.test.ts
git commit -m "feat(web): RLS-aware Drizzle client scoped per user sub"
```

---

## Task 5: The internal Next.js-to-Python contract

**Files:**
- Create: `backend/poseidon/api/internal.py`, `backend/tests/test_internal_dispatch.py`, `web/src/lib/skills.ts`, `web/src/lib/skills.test.ts`
- Modify: `backend/poseidon/api/app.py` — register the new router

**Interfaces:**
- Consumes: the existing `SkillRegistry` and `SkillResult`
- Produces: `POST /internal/v1/skills/{skill_id}/dispatch` accepting `{args, identity: {sub, roles}}` and returning the serialized `SkillResult`; and `dispatchSkill(skillId, args, identity)` in TypeScript

- [ ] **Step 1: Write the failing Python test**

Create `backend/tests/test_internal_dispatch.py`:

```python
import pytest
from fastapi.testclient import TestClient

from poseidon.api.app import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_dispatch_requires_identity(client):
    resp = client.post(
        "/internal/v1/skills/data_qa.metric_query/dispatch",
        json={"args": {}},
    )
    assert resp.status_code == 422


def test_dispatch_rejects_an_unknown_skill(client):
    resp = client.post(
        "/internal/v1/skills/does_not.exist/dispatch",
        json={"args": {}, "identity": {"sub": "dev|local", "roles": ["Poseidon:Sales"]}},
    )
    assert resp.status_code == 404


def test_dispatch_returns_a_skill_result_envelope(client):
    resp = client.post(
        "/internal/v1/skills/data_qa.metric_query/dispatch",
        json={
            "args": {
                "entity": "MARINE_SALES_PLANNING_V",
                "metrics": ["GP"],
                "period": {"start": "2026-04-01", "end": "2026-05-01"},
            },
            "identity": {"sub": "dev|local", "roles": ["Poseidon:Sales"]},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
    assert "parts" in body
```

- [ ] **Step 2: Run it and watch it fail**

Run from `backend/`: `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_internal_dispatch.py -v`
Expected: FAIL — 404 on every route, because the router does not exist.

- [ ] **Step 3: Implement `backend/poseidon/api/internal.py`**

The registry API is verified: `registry.get(skill_id)` **raises `KeyError`** (it does not return
`None`), and `registry.dispatch(skill_id, raw_args, ctx)` takes a `SkillContext` and **never
raises** — a failure comes back as a `SkillResult` with `ok=False`. This mirrors
`backend/poseidon/api/dev_runner.py`, which is the existing precedent for dispatching a skill over
HTTP.

```python
"""The internal contract Next.js dispatches skills through.

This router is NOT public. It exists because the application layer moved to
Next.js (decision M1) while every skill stayed in Python (M2), so the caller
is now another service rather than a browser.

**Identity is passed explicitly and applied here.** app.py's identity
middleware sets ``request.state.user`` on every request, but on THIS route
that user is the web tier's own connection, not the person asking. So the
caller states the subject and roles in the body and this module builds the
``SkillContext`` from those instead -- deliberately ignoring
``request.state.user``. Getting this backwards would scope every user's
query to whatever identity the service connects as.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from poseidon.api.dev_runner import _serialize
from poseidon.core.data.synthetic_client import SyntheticDataClient
from poseidon.core.identity import UserContext
from poseidon.core.skills.context import ConversationSlots, SkillContext

router = APIRouter(prefix="/internal/v1", tags=["internal"])


class _Identity(BaseModel):
    sub: str
    roles: list[str] = Field(default_factory=list)


class _DispatchRequest(BaseModel):
    args: dict
    identity: _Identity


@router.post("/skills/{skill_id}/dispatch")
def dispatch_skill(skill_id: str, body: _DispatchRequest, request: Request) -> dict[str, Any]:
    registry = request.app.state.skill_registry
    try:
        registry.get(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    settings = request.app.state.settings
    ctx = SkillContext(
        data=SyntheticDataClient(settings.database_url),
        artifacts=request.app.state.artifact_store,
        settings=settings,
        state=ConversationSlots(),
        user=UserContext(
            sub=body.identity.sub,
            email=None,
            name=None,
            roles=tuple(body.identity.roles),
        ),
    )
    return _serialize(registry.dispatch(skill_id, body.args, ctx))
```

**Two things to check before writing this**, because they decide whether the code above compiles:

1. `create_app` builds `app.state.skill_registry` only when the dev runner is enabled (see
   `dev_runner.py`'s module docstring). This router needs it **always**. Read `app.py`'s startup and
   move the `SkillRegistry.discover()` call out of the dev-only branch if it is inside one.
2. `_serialize` is currently private to `dev_runner`. Importing a private name across modules is a
   smell — if it is more than a few lines, promote it to a shared module and have both routers use
   it rather than duplicating the `ArtifactRef` flattening.

- [ ] **Step 4: Register the router in `backend/poseidon/api/app.py`**

Add beside the other `include_router` calls, and **before** any StaticFiles mount:

```python
from poseidon.api import internal
app.include_router(internal.router)
```

- [ ] **Step 5: Run the Python tests and watch them pass**

Run from `backend/`: `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_internal_dispatch.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Write the failing TypeScript test**

Create `web/src/lib/skills.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { dispatchSkill } from "./skills";

describe("dispatchSkill", () => {
  it("posts args and identity to the internal route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, parts: [], proof: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await dispatchSkill("data_qa.metric_query", { metrics: ["GP"] }, {
      sub: "dev|local",
      roles: ["Poseidon:Sales"],
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/internal/v1/skills/data_qa.metric_query/dispatch");
    expect(JSON.parse(init.body)).toEqual({
      args: { metrics: ["GP"] },
      identity: { sub: "dev|local", roles: ["Poseidon:Sales"] },
    });
  });

  it("throws on a non-200 so a failure is never mistaken for an empty result", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, text: async () => "boom" }));
    await expect(
      dispatchSkill("data_qa.metric_query", {}, { sub: "dev|local", roles: [] }),
    ).rejects.toThrow(/500/);
  });
});
```

- [ ] **Step 7: Run it and watch it fail**

Run: `cd web && npx vitest run src/lib/skills.test.ts`
Expected: FAIL — `./skills` not found.

- [ ] **Step 8: Implement `web/src/lib/skills.ts`**

```ts
import type { Identity } from "./identity";

const ANALYTICS_ORIGIN = process.env.ANALYTICS_ORIGIN ?? "http://localhost:8000";
const TIMEOUT_MS = Number(process.env.ANALYTICS_TIMEOUT_MS ?? 30_000);

export type SkillResult = {
  ok: boolean;
  parts: unknown[];
  proof: unknown[];
};

export async function dispatchSkill(
  skillId: string,
  args: Record<string, unknown>,
  identity: Pick<Identity, "sub" | "roles">,
): Promise<SkillResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(
      `${ANALYTICS_ORIGIN}/internal/v1/skills/${skillId}/dispatch`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args, identity: { sub: identity.sub, roles: identity.roles } }),
        signal: controller.signal,
      },
    );
    if (!resp.ok) {
      throw new Error(`skill dispatch failed: ${resp.status} ${await resp.text()}`);
    }
    return (await resp.json()) as SkillResult;
  } finally {
    clearTimeout(timer);
  }
}
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `cd web && npx vitest run src/lib/skills.test.ts`
Expected: PASS, 2 tests.

- [ ] **Step 10: Prove the seam for real, not mocked from both sides**

Both halves are tested apart — Python with `TestClient`, TypeScript against a stubbed `fetch`.
Neither proves they talk to each other. Join them once:

```bash
# terminal 1
docker compose -f infra/docker-compose.yml up -d
# terminal 2, from web/
node --input-type=module -e "
import { dispatchSkill } from './src/lib/skills.ts';
const r = await dispatchSkill('data_qa.metric_query', {
  entity: 'MARINE_SALES_PLANNING_V',
  metrics: ['GP'],
  period: { start: '2026-04-01', end: '2026-05-01' },
}, { sub: 'dev|local', roles: ['Poseidon:Sales'] });
console.log(JSON.stringify(r).slice(0, 400));
"
```

Expected: a `SkillResult` envelope with `ok` and `parts`, printed from the **live** FastAPI
service. If Node cannot import the `.ts` directly on this setup, run it through the app instead —
any route that calls `dispatchSkill` once. Record the actual output in the report; "it should work"
is not evidence.

- [ ] **Step 11: Commit**

```bash
git add backend/poseidon/api/internal.py backend/tests/test_internal_dispatch.py backend/poseidon/api/app.py web/src/lib/skills.ts web/src/lib/skills.test.ts
git commit -m "feat: internal skill-dispatch contract between the web tier and Python"
```

---

## Task 6: Read an existing conversation end to end

**Files:**
- Create: `web/src/app/page.tsx`, `web/src/app/c/[id]/page.tsx`, `web/src/lib/conversations.ts`, `web/src/lib/conversations.test.ts`

**Interfaces:**
- Consumes: Tasks 2, 3, 4
- Produces: the phase gate

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/conversations.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { listConversations, loadConversation } from "./conversations";

const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("conversations", () => {
  it("lists only the caller's conversations", async () => {
    const alice = await listConversations("dev|alice");
    expect(alice.every((c) => c.userSub === "dev|alice")).toBe(true);
  });

  it("returns null for a conversation the caller does not own", async () => {
    const [first] = await listConversations("dev|alice");
    if (!first) return;
    expect(await loadConversation("dev|bob", first.id)).toBeNull();
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd web && DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5432/poseidon npx vitest run src/lib/conversations.test.ts`
Expected: FAIL — `./conversations` not found.

- [ ] **Step 3: Implement `web/src/lib/conversations.ts`**

```ts
import { asc, eq } from "drizzle-orm";
import { withUser } from "../db/client";
import { conversations, messages } from "../db/schema";

export async function listConversations(sub: string) {
  return withUser(sub, (tx) =>
    tx.select().from(conversations).orderBy(asc(conversations.updatedAt)),
  );
}

export async function loadConversation(sub: string, id: string) {
  return withUser(sub, async (tx) => {
    const [conversation] = await tx
      .select()
      .from(conversations)
      .where(eq(conversations.id, id));
    // RLS already filtered this -- a foreign id simply returns no row rather
    // than raising, so an absent row means "not yours or not there", and the
    // caller cannot tell the difference. That is the intended behaviour.
    if (!conversation) return null;
    const rows = await tx
      .select()
      .from(messages)
      .where(eq(messages.conversationId, id))
      .orderBy(asc(messages.createdAt));
    return { conversation, messages: rows };
  });
}
```

Correct the column names against the generated `schema.ts` if they differ.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd web && DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5432/poseidon npx vitest run src/lib/conversations.test.ts`
Expected: PASS

- [ ] **Step 5: Build the two pages**

`web/src/app/page.tsx`:

```tsx
import Link from "next/link";
import { headers } from "next/headers";
import { listConversations } from "../lib/conversations";

export default async function Home() {
  const h = await headers();
  const sub = h.get("x-poseidon-sub") ?? "dev|local";
  const rows = await listConversations(sub);
  return (
    <main className="p-8">
      <h1 className="text-xl font-semibold">Conversations for {sub}</h1>
      <ul className="mt-4 space-y-2">
        {rows.map((c) => (
          <li key={c.id}>
            <Link className="underline" href={`/c/${c.id}`}>
              {c.title ?? "(untitled)"}
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
```

`web/src/app/c/[id]/page.tsx`:

```tsx
import { notFound } from "next/navigation";
import { headers } from "next/headers";
import { loadConversation } from "../../../lib/conversations";

export default async function Conversation({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const h = await headers();
  const sub = h.get("x-poseidon-sub") ?? "dev|local";
  const loaded = await loadConversation(sub, id);
  if (!loaded) notFound();
  return (
    <main className="p-8">
      <h1 className="text-xl font-semibold">{loaded.conversation.title ?? "(untitled)"}</h1>
      <ol className="mt-4 space-y-4">
        {loaded.messages.map((m) => (
          <li key={m.id}>
            <span className="text-xs uppercase opacity-60">{m.role}</span>
            <pre className="whitespace-pre-wrap text-sm">{JSON.stringify(m.parts, null, 2)}</pre>
          </li>
        ))}
      </ol>
    </main>
  );
}
```

- [ ] **Step 6: Run the phase gate by hand**

1. `docker compose -f infra/docker-compose.yml up -d` — full stack.
2. Open `http://localhost:5173` and send one chat turn, so a real conversation with history exists.
3. `cd web && npm run dev`, open `http://localhost:3000`.
4. The conversation from step 2 appears in the list. Click it — its messages render.
5. Restart the Next dev server and reload. The history is still there.
6. `http://localhost:5173` still works.

- [ ] **Step 7: Run every suite**

```bash
cd web && npx vitest run
cd ../backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live'
cd ../frontend && npm test -- --run
```

All three green. The Vite app's suite must be untouched by this phase — if it changed, something was modified that should not have been.

- [ ] **Step 8: Commit**

```bash
git add web/src/app web/src/lib/conversations.ts web/src/lib/conversations.test.ts
git commit -m "feat(web): list and open an existing conversation under RLS"
```

---

## Deviations from the migration design, and why

Two places where this plan does **not** do what the spec's Phase 15 description says. Both are
deliberate; overrule either if you disagree.

**1. Auth.js is not used.** The spec's Phase 15 says "Auth.js wired to the chosen provider." After
M5 there is no OAuth provider — production identity is a platform-forwarded header and local is a
fixed user. Auth.js would contribute nothing to either path, so this plan resolves identity directly
in Next 16's `proxy.ts`, which is where that logic belongs anyway. Auth.js remains in the confirmed
stack (M1) and can be introduced the moment a real provider is. This is the simplification already
flagged in the migration design §6.

**2. Drizzle does not take over migrations in this phase.** The spec's Phase 15 says the authority
decision (Q3) is "applied." This plan applies only the *reading* half: introspect the live schema,
prove it round-trips, and record in the generated file what introspection cannot see. Handing
migration authority to Drizzle means porting migrations 0009 and 0010's **grants and roles** —
`poseidon_worker`, the `poseidon_app` membership, the RLS policies, `FORCE ROW LEVEL SECURITY` —
none of which `drizzle-kit pull` captures, and all of which exist because RDS has no superuser. That
is its own task with its own gate, and doing it half-way inside a scaffolding phase is how a
database loses its access controls quietly. Proposed as the first task of Phase 16.

## Phase gate

**What Carlos clicks:** open `http://localhost:3000`, see the conversations he created in the old app at `http://localhost:5173`, click one, read its history. Both apps running at once.

**What that proves:** identity produces the same subjects Python does, Drizzle reads the real schema, RLS holds through a second client, and Python still owns the skills — every seam of the migration, with no new features to confuse the signal.

## Explicitly not in this phase

Streaming and AI SDK (Phase 16), any report work (Phase 17), sending a chat turn from Next.js, Drizzle taking over migrations, deleting anything from the Vite app, and Auth0 in any form.

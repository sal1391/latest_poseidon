import type { Identity } from "./identity";

/**
 * The client half of the internal Next.js-to-Python contract
 * (`backend/poseidon/api/internal.py`). Python keeps every skill after the
 * migration (decision M2), so this is how the application tier asks for one.
 *
 * **Identity travels in the body.** The route does not — must not — read the
 * identity of the connection it is called over: that connection belongs to
 * this service, not to the person asking. The caller is therefore responsible
 * for having resolved a real identity first (`proxy.ts` does, per request) and
 * for passing it through verbatim. See `internal.py`'s own module docstring
 * for the trust boundary that makes this safe, and for why it has to be a
 * network boundary rather than a check inside the route.
 */

/**
 * `dev_runner._serialize`'s wire shape, field for field
 * (`backend/poseidon/api/dev_runner.py:91-99`) — all five keys, not the three
 * a happy path happens to read. `parts`/`proof`/`artifacts` are deliberately
 * `unknown[]`: the ten part kinds are a rendering contract this module has no
 * business duplicating, and a wrong local copy of it would be worse than none.
 *
 * `error` carries an RFC-7807 problem detail whenever `ok` is false
 * (`core/skills/result.py`'s `problem()`), and `ok: false` is a real answer —
 * it arrives inside an HTTP 200 and this client returns it rather than
 * throwing. Only a response that means *no skill ran* throws.
 */
export type SkillResult = {
  ok: boolean;
  parts: unknown[];
  proof: unknown[];
  artifacts: unknown[];
  error: Record<string, unknown> | null;
};

const DEFAULT_ORIGIN = "http://localhost:8000";
const DEFAULT_TIMEOUT_MS = 30_000;

/**
 * Where Python answers.
 *
 * Read per call, not once at module load, matching this codebase's other two
 * readers of the same kind of value (`proxy.ts`'s `IDENTITY_MODE`,
 * `next.config.ts`'s `BACKEND_ORIGIN`) — a module-scope snapshot is taken
 * before anything a runtime loads later, and cannot be exercised by a test at
 * all.
 *
 * `BACKEND_ORIGIN` is honoured as a fallback because `next.config.ts:11`
 * already names this exact service, with this exact default, under that name
 * for the `/api/*` rewrite. Two independent variables for one origin fail in
 * the quietest possible way: an operator who sets only the one the rewrite
 * needs gets a working UI whose skill dispatches all go to localhost.
 */
function analyticsOrigin(): string {
  return process.env.ANALYTICS_ORIGIN ?? process.env.BACKEND_ORIGIN ?? DEFAULT_ORIGIN;
}

/**
 * A skill dispatch is a database query plus rendering, so the ceiling is
 * seconds, not the platform default of none at all: without one, a wedged
 * analytics runtime holds this request open until something further up gives
 * up, with no log line saying why.
 *
 * A non-numeric or non-positive override falls back rather than being used:
 * `Number("")` is 0 and `setTimeout(0)` aborts the request before it is sent,
 * which would look exactly like the backend being down.
 */
function timeoutMs(): number {
  const raw = process.env.ANALYTICS_TIMEOUT_MS;
  const parsed = raw === undefined ? NaN : Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_TIMEOUT_MS;
}

export async function dispatchSkill(
  skillId: string,
  args: Record<string, unknown>,
  identity: Pick<Identity, "sub" | "roles">,
): Promise<SkillResult> {
  const limit = timeoutMs();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), limit);
  // `encodeURIComponent`, even though every registered id is `task.skill` and
  // passes through it unchanged (`.` and `_` are not encoded): an id is a
  // value, and a value interpolated raw into a path can leave the route it was
  // meant for. A `/` in one would address a different endpoint entirely and
  // the 404 would name the wrong thing.
  const url = `${analyticsOrigin()}/internal/v1/skills/${encodeURIComponent(skillId)}/dispatch`;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      // Only `sub` and `roles`: the route builds its `UserContext` with
      // `email`/`name` as `None` regardless, so sending them would state a
      // contract that does not exist. Written out field by field rather than
      // spreading `identity`, because `Pick` narrows the type and not the
      // object — a full `Identity` handed to this function at runtime still
      // carries every field it has.
      body: JSON.stringify({ args, identity: { sub: identity.sub, roles: identity.roles } }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      // The body, not just the status. The route's own 404 and 501 details
      // name the unregistered skill id and the misconfigured `data_backend`;
      // a message carrying only the number turns both into the same line.
      throw new Error(`skill dispatch failed: ${resp.status} ${await resp.text()}`);
    }
    return (await resp.json()) as SkillResult;
  } catch (err) {
    // The only thing that aborts this request is the timer above, so an abort
    // here is unambiguous — and worth naming, since the platform's own
    // `AbortError` says nothing about which deadline elapsed or how long it
    // was.
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(`skill dispatch timed out after ${limit}ms: ${skillId}`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

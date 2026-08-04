import type { Conversation, Message, MessagePart, Page, SkillSummary } from "./types";

/** RFC-7807 problem-details shape every backend failure in this codebase
 * renders through (poseidon.core.skills.result.problem; api/auth.py's
 * AuthError/RateLimitExceeded handlers) -- see features/auth/* for where
 * the frontend renders one of these directly for a caller to read. */
export interface ProblemDetail {
  type: string;
  title: string;
  detail: string;
  status: number;
}

/** GET /api/me's wire shape (poseidon.api.auth.get_me -- doc 05 section 2's
 * frontend seam). `identity_mode` mirrors core/config.py's own
 * Settings.identity_mode Literal. */
export interface Identity {
  sub: string;
  name: string | null;
  email: string | null;
  roles: string[];
  identity_mode: "disabled" | "auth0" | "spcs_ingress";
}

/** Thrown by `apiFetch` for any non-2xx response. Carries the parsed
 * RFC-7807 body when the response actually sent one -- every backend
 * failure in this codebase does; `problem` is `null` only when the body
 * genuinely is not JSON (a non-backend failure, e.g. a proxy error page).
 * `features/auth/AuthGate.tsx` is the one caller that inspects `.status`/
 * `.problem` directly (to tell a real 401 apart from an unreachable
 * backend, and to render the backend's own title/detail verbatim); every
 * other existing caller only reads `.message` (an `Error` subclass), so
 * this is a backward-compatible narrowing of what `apiFetch` used to
 * throw, not a breaking change to its contract. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetail | null;

  constructor(status: number, problem: ProblemDetail | null) {
    super(`request failed: ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

/** The auth-header injector (Phase 9 Task 4, doc 05 section 3): sees a
 * fresh token on every call rather than a snapshot taken once at login --
 * `features/auth/Auth0Boundary.tsx` wires this to a closure over the SDK's
 * own `getAccessTokenSilently`, which itself refreshes an expired token
 * transparently, so every request always carries a live token with no
 * separate refresh plumbing needed here. `null` (the default: no caller
 * has authenticated yet, or `disabled`/`spcs_ingress` mode, which resolve
 * identity from something other than a bearer token) means "send no
 * Authorization header" -- omitting it is correct behavior for those
 * modes, never a degraded one. */
export type TokenProvider = () => Promise<string | null>;

let tokenProvider: TokenProvider | null = null;

export function setAuthTokenProvider(fn: TokenProvider | null): void {
  tokenProvider = fn;
}

/**
 * The ONE request builder every outbound call in this app funnels
 * through: `apiFetch` below AND `api/sse.ts`'s `streamTurn` both call
 * this directly rather than `fetch` themselves -- closing the
 * long-standing carryforward (two separate fetch call sites meant the
 * auth-header injector could silently miss one of them; see this task's
 * own report for the RED proof that a `streamTurn` bypassing this
 * function fails the "one builder" test). Attaches `Authorization: Bearer
 * <token>` when a token provider is set and currently returns one;
 * otherwise sends the request exactly as given.
 */
export async function requestWithAuth(input: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (tokenProvider) {
    const token = await tokenProvider();
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(input, { ...init, headers });
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const r = await requestWithAuth(input, init);
  if (!r.ok) {
    const problem = (await r.json().catch(() => null)) as ProblemDetail | null;
    throw new ApiError(r.status, problem);
  }
  return r;
}

/** doc 05 section 2's frontend seam: called once on boot
 * (`features/auth/AuthGate.tsx`) and again immediately after a successful
 * Auth0 login, once the injector carries a real token. */
export async function getMe(): Promise<Identity> {
  const r = await apiFetch("/api/me");
  return (await r.json()) as Identity;
}

export async function createConversation(): Promise<{ conversation: Conversation; opener: Message }> {
  const r = await apiFetch("/api/conversations", { method: "POST" });
  return (await r.json()) as { conversation: Conversation; opener: Message };
}

/** Appends `?cursor=` when a cursor is given; the first page of either list
 * endpoint below is requested with none at all, matching `live_chat.py`'s
 * own "absent means first page" contract (never an empty-string cursor). */
function withCursor(path: string, cursor?: string): string {
  return cursor ? `${path}?cursor=${encodeURIComponent(cursor)}` : path;
}

export async function listConversations(cursor?: string): Promise<Page<Conversation>> {
  const r = await apiFetch(withCursor("/api/conversations", cursor));
  return (await r.json()) as Page<Conversation>;
}

export async function getMessages(cid: string, cursor?: string): Promise<Page<Message>> {
  const r = await apiFetch(withCursor(`/api/conversations/${cid}/messages`, cursor));
  return (await r.json()) as Page<Message>;
}

export async function postFeedback(
  mid: string,
  verdict: "up" | "down" | null,
  comment?: string,
): Promise<void> {
  await apiFetch(`/api/messages/${mid}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ verdict, comment: comment ?? null }),
  });
}

/** GET /api/messages/{mid}/feedback's wire shape (poseidon.api.live_chat.
 * get_feedback, Phase 12 Task 1): 200 with the recorded verdict, or a 404
 * this function does not special-case -- like every other reader in this
 * file, a non-2xx becomes a thrown `ApiError` and the CALLER decides what it
 * means. `chatStore.ts`'s `hydrateFeedback` is the one caller today, and it
 * treats any 404 (the route does not distinguish "no feedback yet" from
 * "message invisible" in the body) as simply nothing to hydrate. */
export async function getFeedback(
  mid: string,
): Promise<{ verdict: "up" | "down"; comment: string | null }> {
  const r = await apiFetch(`/api/messages/${mid}/feedback`);
  return (await r.json()) as { verdict: "up" | "down"; comment: string | null };
}

/** Live-chat-only (poseidon.api.live_chat's own module docstring) -- mock
 * mode has no such route, so a 404 here is expected, not exceptional; the
 * caller (SkillsPicker) is the one that decides what "no answer" means. */
export async function listSkills(): Promise<SkillSummary[]> {
  const r = await apiFetch("/api/skills");
  return (await r.json()) as SkillSummary[];
}

/** GET /api/turns/{id}'s pinned wire shape (poseidon.api.turns.get_turn,
 * doc 01 section 5) -- reconnect reconciliation, Phase 11 Task 3. Field
 * names mirror the backend verbatim (this codebase's own DTO convention --
 * see this file's module-level types above), never renamed to camelCase. */
export interface TurnSummary {
  id: string;
  conversation_id: string | null;
  message_id: string | null;
  kind: string;
  status: string;
  question: string | null;
  mode: string | null;
  created_at: string;
  finished_at: string | null;
  trace_id: string | null;
  redacted: boolean;
}
export interface LlmCallSummary {
  seq: number;
  provider: string;
  model_id: string;
  role: string;
  prompt_version: string;
  status: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number | null;
}
export interface ToolCallSummary {
  seq: number;
  tool: string;
  server: string | null;
  status: string;
  latency_ms: number | null;
}
export interface TurnDetail {
  turn: TurnSummary;
  llm_calls: LlmCallSummary[];
  tool_calls: ToolCallSummary[];
  message: { id: string; parts: MessagePart[] } | null;
}

/** Live-chat-only, like `listSkills` above. The one caller today is
 * `chatStore.ts`'s own on-drop reconcile hook (doc 01 section 5, client
 * rule 3) -- a stream that errors mid-turn with a known turn_id fetches
 * this to materialize whatever the run log already captured. */
export async function getTurn(turnId: string): Promise<TurnDetail> {
  const r = await apiFetch(`/api/turns/${turnId}`);
  return (await r.json()) as TurnDetail;
}

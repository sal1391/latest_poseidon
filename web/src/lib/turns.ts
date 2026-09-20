import { analyticsOrigin, internalHeaders } from "./skills";

/**
 * The streaming half of the internal Next.js-to-Python contract
 * (`backend/poseidon/api/internal.py`'s `stream_conversation_turn`), beside
 * `dispatchSkill`'s request/response half in `./skills`.
 *
 * Everything about the seam is `skills.ts`'s -- the origin, the shared-secret
 * header, the fresh trace id, and above all the rule that **identity travels
 * in the body**: the connection this is sent over belongs to the web service,
 * not to the person asking, so the caller must have resolved a real identity
 * first (`requireSales` on the route's own headers, which `proxy.ts` set) and
 * pass it through verbatim.
 *
 * Two things it deliberately does NOT do:
 *
 *  - **No timeout.** `dispatchSkill`'s 30s ceiling is right for a request that
 *    either answers or does not; a turn streams for as long as the work takes
 *    and a wall-clock deadline would cut a healthy long turn in half. The
 *    caller supplies an IDLE deadline instead, as `signal` (spec section 10),
 *    which is the only shape of deadline a stream can carry honestly.
 *  - **No status check.** A non-2xx is the caller's to forward: Python's
 *    pre-stream refusals (404 unknown conversation, 429 rate limit, 503
 *    `CHAT_MODE=live` required) carry RFC-7807 bodies that the browser should
 *    see, and a throw here would flatten all of them into one 500.
 */
export async function streamTurn(
  cid: string,
  body: { text: string; client_turn_key: string | null },
  identity: { sub: string; roles: string[] },
  signal?: AbortSignal,
): Promise<Response> {
  const headers = internalHeaders(); // throws before any request when the token is unset
  // `encodeURIComponent` for the same reason `dispatchSkill` encodes a skill
  // id: a conversation id is a value, and a value interpolated raw into a path
  // can address a different endpoint entirely.
  const url = `${analyticsOrigin()}/internal/v1/conversations/${encodeURIComponent(cid)}/turns`;
  return fetch(url, {
    method: "POST",
    headers,
    // Field by field, never a spread of `identity`: the type narrows the shape
    // but not the object, and Python's `_Identity` has exactly these two.
    body: JSON.stringify({
      identity: { sub: identity.sub, roles: identity.roles },
      text: body.text,
      client_turn_key: body.client_turn_key,
    }),
    signal,
  });
}

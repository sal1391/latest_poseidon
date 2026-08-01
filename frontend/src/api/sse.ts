import { requestWithAuth } from "./client";
import type { SseEvent } from "./types";

/**
 * Thrown by `streamTurn` for any failure -- a non-2xx/missing-body response,
 * or the read loop itself rejecting mid-stream (a network drop). `turnId` is
 * the last `turn_id` this stream actually saw (from the envelope of any
 * frame processed before the failure), or `null` when nothing arrived yet --
 * Phase 11 Task 3 (doc 01 section 5, client rule 3): `chatStore.ts`'s own
 * catch block reads this back to decide whether a `GET /api/turns/{id}`
 * reconcile is even possible, never guessing at an id this module never saw.
 */
export class StreamError extends Error {
  readonly turnId: string | null;

  constructor(message: string, turnId: string | null) {
    super(message);
    this.name = "StreamError";
    this.turnId = turnId;
  }
}

export function parseSseChunk(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let name = "";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) name = line.slice(7);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
    if (!name || dataLines.length === 0) continue;
    try {
      events.push({ name, data: JSON.parse(dataLines.join("\n")) } as SseEvent);
    } catch {
      // Malformed frame: skip it rather than killing the stream; the envelope's
      // event_seq lets turn reconciliation recover anything dropped.
      continue;
    }
  }
  return { events, rest };
}

export async function streamTurn(
  cid: string,
  text: string,
  // Phase 10 Task 4 (poseidon-carryforwards.md's "Phase 6" entry, closed):
  // minting moved OUT of this module and into `chatStore.ts`'s own
  // `sendMessage`, which mints exactly once per logical send and can be
  // handed the SAME key again for a retry of that same send -- the
  // backend's `(user_sub, client_turn_key)` short-circuit in
  // orchestrator.py's `_begin_turn` depends on retries reusing it. A
  // required parameter (not defaulted here) so this module can no longer
  // silently mint a fresh one and defeat that idempotency check.
  clientTurnKey: string,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  // Routed through the SAME shared request builder `api/client.ts`'s
  // `apiFetch` uses (the P9 carryforward this task closes: a separate
  // `fetch` call here used to mean the auth-header injector could add the
  // header to every REST call and silently miss the one SSE call site --
  // see client.test.ts's own "one shared builder" pin).
  const response = await requestWithAuth(`/api/conversations/${cid}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, client_turn_key: clientTurnKey }),
    signal,
  });
  if (!response.ok || !response.body) {
    // Failed before a single frame arrived -- no turn_id has ever been
    // seen, so there is nothing a GET /api/turns/{id} reconcile could look
    // up yet.
    throw new StreamError(`turn failed: ${response.status}`, null);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // The last turn_id this stream actually saw -- every SseEvent's envelope
  // carries one unconditionally (types.ts's own SseEnvelope), so the very
  // first frame (always "accepted") already sets this before anything else
  // can go wrong.
  let lastTurnId: string | null = null;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseChunk(buffer);
      buffer = parsed.rest;
      for (const e of parsed.events) {
        lastTurnId = e.data.turn_id;
        onEvent(e);
      }
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new StreamError(message, lastTurnId);
  }
}

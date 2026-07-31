import { requestWithAuth } from "./client";
import type { SseEvent } from "./types";

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
    body: JSON.stringify({ text, client_turn_key: crypto.randomUUID() }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`turn failed: ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseChunk(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
}

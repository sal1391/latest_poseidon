import { readUIMessageStream, type UIMessageChunk } from "ai";
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

/**
 * The inverse of the SDK's own response encoder (`JsonToSseTransformStream`):
 * one `data: {json}` line per UI chunk, terminated by `data: [DONE]`. `ai`
 * exports the ENCODER but no decoder -- `parseJsonEventStream` lives in
 * `@ai-sdk/provider-utils`, a transitive dependency this package does not
 * declare -- so this is the small local reader the brief allows, kept to the
 * one job of undoing that encoding.
 */
function uiChunksFromSse(res: Response): ReadableStream<UIMessageChunk> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  return new ReadableStream<UIMessageChunk>({
    async pull(controller) {
      for (;;) {
        const boundary = buffer.indexOf("\n\n");
        if (boundary !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          for (const line of block.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6);
            if (payload === "[DONE]") continue;
            controller.enqueue(JSON.parse(payload) as UIMessageChunk);
          }
          return;
        }
        const { value, done } = await reader.read();
        if (done) {
          controller.close();
          return;
        }
        buffer += decoder.decode(value, { stream: true });
      }
    },
  });
}

const salesHeaders = { "x-poseidon-sub": "dev|local", "x-poseidon-roles": "Poseidon:Sales", "content-type": "application/json" };
const params = Promise.resolve({ cid: "c1" });

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });

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

  it("forwards the conversation id, text and client_turn_key it was called with", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "t");
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(SSE));
    vi.stubGlobal("fetch", fetchMock);
    const res = await POST(
      new Request("http://x/api/chat/c1", { method: "POST", headers: salesHeaders, body: JSON.stringify({ text: "hi", client_turn_key: "k" }) }),
      { params },
    );
    await res.text(); // drain, so the handler's reader loop finishes and its idle timer is cleared
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/internal/v1/conversations/c1/turns");
    expect(JSON.parse(init.body)).toEqual({
      identity: { sub: "dev|local", roles: ["Poseidon:Sales"] },
      text: "hi",
      client_turn_key: "k",
    });
  });

  // `client_turn_key` is optional on Python's `_TurnRequest`, and a body
  // without one must reach it as an explicit null rather than as `undefined`,
  // which `JSON.stringify` would drop from the body entirely.
  it("defaults a missing client_turn_key to null", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "t");
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(SSE));
    vi.stubGlobal("fetch", fetchMock);
    const res = await POST(
      new Request("http://x/api/chat/c1", { method: "POST", headers: salesHeaders, body: JSON.stringify({ text: "hi" }) }),
      { params },
    );
    await res.text();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).client_turn_key).toBeNull();
  });

  // P16-R15/R? -- a stream that dies mid-turn must reach the page as an error
  // the composer can show, not as a silently short message: `useChat` surfaces
  // an `error` chunk through its `error` return value.
  it("turns a cut-off upstream stream into an error chunk", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "t");
    const truncated = SSE.slice(0, SSE.indexOf('id: 4')); // accepted, part, token -- no terminal frame
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(truncated)));
    const res = await POST(new Request("http://x/api/chat/c1", { method: "POST", headers: salesHeaders, body: JSON.stringify({ text: "hi", client_turn_key: null }) }), { params });
    const body = await res.text();
    expect(body).toContain('"type":"error"');
    expect(body).toContain("turn stream ended without a done or error frame");
  });

  /**
   * The idle timer is armed BEFORE the upstream call, so every way out of the
   * handler has to disarm it. The streaming path does so in its `finally` and
   * the non-2xx path inline; a `streamTurn` that throws outright -- an unset
   * token, a refused connection -- had neither, and left a live two-minute
   * timer behind per failed request. That is unbounded accumulation on exactly
   * the path a misconfigured deployment takes for every request it receives.
   */
  it("clears the idle timer when the upstream call itself throws", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "t");
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    await expect(
      POST(new Request("http://x/api/chat/c1", { method: "POST", headers: salesHeaders, body: JSON.stringify({ text: "hi", client_turn_key: null }) }), { params }),
    ).rejects.toThrow(/ECONNREFUSED/);
    expect(vi.getTimerCount()).toBe(0);
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

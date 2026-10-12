import { describe, expect, it } from "vitest";
import { readUIMessageStream, type UIMessageChunk } from "ai";
import { createFrameTranslator, type EnvelopeFrame } from "./turn-stream";
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

// P16-R17. The translator and the adapter together have to land on exactly what
// Python's TurnTranscriptBuffer would have persisted for the same frames. These
// two traces are the interleavings the corpus does not contain (no producer emits
// more than one token run per turn today -- orchestrator.py calls sink.push_token
// once -- so the goldens cannot pin this, but the two folds must still agree).
async function replayFrames(frames: EnvelopeFrame[]): Promise<PoseidonUIMessage> {
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

const env = { turn_id: "turn-1", message_id: "msg-1" };
const f = (event: string, seq: number, extra: Record<string, unknown> = {}): EnvelopeFrame => ({
  event,
  data: { ...env, event_seq: seq, ...extra },
});

describe("translator + adapter fold parity with Python's fold_token", () => {
  it("a part frame between two token runs yields [text][part][text], not one merged text part", async () => {
    const message = await replayFrames([
      f("accepted", 1, { turn_index: 1 }),
      f("token", 2, { text: "a" }),
      f("part", 3, { kind: "table", payload: { columns: ["c"], rows: [[1]] } }),
      f("token", 4, { text: "b" }),
      f("done", 5, { usage: {} }),
    ]);
    expect(message.parts.map((p) => p.type)).toEqual(["text", "data-table", "text"]);
    expect(toMessageParts(message)).toEqual([
      { kind: "text", payload: { markdown: "a" } },
      { kind: "table", payload: { columns: ["c"], rows: [[1]] } },
      { kind: "text", payload: { markdown: "b" } },
    ]);
  });

  it("a token after a text part frame folds into it, exactly as fold_token appends to a trailing text part", async () => {
    const message = await replayFrames([
      f("accepted", 1, { turn_index: 1 }),
      f("token", 2, { text: "a" }),
      f("part", 3, { kind: "text", payload: { markdown: "B" } }),
      f("token", 4, { text: "c" }),
      f("done", 5, { usage: {} }),
    ]);
    expect(message.parts.map((p) => p.type)).toEqual(["text", "data-text", "text"]);
    expect(toMessageParts(message)).toEqual([
      { kind: "text", payload: { markdown: "a" } },
      { kind: "text", payload: { markdown: "Bc" } },
    ]);
  });
});

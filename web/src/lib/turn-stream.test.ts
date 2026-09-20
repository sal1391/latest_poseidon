import { describe, expect, it } from "vitest";
import { createFrameTranslator, parseSseChunk, textPartId, type EnvelopeFrame } from "./turn-stream";

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
      { type: "text-start", id: textPartId(1) },
      { type: "text-delta", id: textPartId(1), delta: "Hel" },
    ]);
    expect(t.translate(f("token", 3, { text: "lo" }))).toEqual([{ type: "text-delta", id: textPartId(1), delta: "lo" }]);
    expect(t.translate(f("done", 4, { usage: { input_tokens: 1 }, title: "T" }))).toEqual([
      { type: "text-end", id: textPartId(1) },
      { type: "finish", messageMetadata: { turn_id: "turn-1", status: "ok", usage: { input_tokens: 1 }, title: "T" } },
    ]);
    expect(t.end()).toEqual([]);
  });

  // P16-R17. Python's fold_token appends to the trailing part only when that part
  // is text, so a part frame between two token runs leaves [text][part][text]. A
  // single turn-wide text id folded the second run back into the first part, ahead
  // of the part frame; per-run ids make the boundary explicit on the wire.
  it("a non-token frame closes the open text run, and the next token opens a new one", () => {
    const t = createFrameTranslator();
    expect(t.translate(f("token", 2, { text: "a" }))).toEqual([
      { type: "text-start", id: textPartId(1) },
      { type: "text-delta", id: textPartId(1), delta: "a" },
    ]);
    expect(t.translate(f("part", 3, { kind: "table", payload: { columns: ["c"], rows: [[1]] } }))).toEqual([
      { type: "text-end", id: textPartId(1) },
      { type: "data-table", data: { columns: ["c"], rows: [[1]] } },
    ]);
    expect(t.translate(f("token", 4, { text: "b" }))).toEqual([
      { type: "text-start", id: textPartId(2) },
      { type: "text-delta", id: textPartId(2), delta: "b" },
    ]);
    expect(t.translate(f("done", 5, { usage: {} }))).toEqual([
      { type: "text-end", id: textPartId(2) },
      { type: "finish", messageMetadata: { turn_id: "turn-1", status: "ok", usage: {}, title: undefined } },
    ]);
  });

  it("tool and phase frames close an open text run too, before their own chunk", () => {
    const t = createFrameTranslator();
    t.translate(f("token", 2, { text: "a" }));
    expect(t.translate(f("tool", 3, { tool_seq: 1, tool: "x", server: null, status: "start", label: "X" }))).toEqual([
      { type: "text-end", id: textPartId(1) },
      { type: "data-tool_event", id: "tool-1", data: { tool_seq: 1, tool: "x", server: null, status: "start", label: "X" } },
    ]);
    t.translate(f("token", 4, { text: "b" }));
    expect(t.translate(f("phase", 5, { phase: "answer", status: "running" }))).toEqual([
      { type: "text-end", id: textPartId(2) },
      { type: "data-phase", id: "answer", data: { phase: "answer", status: "running" }, transient: true },
    ]);
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
      { type: "text-end", id: textPartId(1) },
      { type: "error", errorText: "turn stream ended without a done or error frame" },
    ]);
  });
});

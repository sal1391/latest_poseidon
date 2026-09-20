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

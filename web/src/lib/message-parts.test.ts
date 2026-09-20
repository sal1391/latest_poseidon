import { describe, expect, it } from "vitest";
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

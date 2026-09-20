import type { UIMessage } from "ai";

export type MessagePart = { kind: string; payload: unknown };
export type TurnMetadata = {
  turn_id?: string;
  turn_index?: number;
  status?: "ok" | "error";
  usage?: unknown;
  title?: string | null;
};
export type PoseidonUIMessage = UIMessage<TurnMetadata, Record<string, unknown>>;

const SKIPPED = new Set(["step-start", "data-phase", "reasoning", "source-url", "source-document", "file"]);

/**
 * UI message -> Poseidon's persisted shape. Mirrors backend/poseidon/api/
 * live_chat.py's fold (`_record_transcript_frame` + TurnTranscriptBuffer):
 * a `part` is appended verbatim, a `tool` upserts by tool_seq (the SDK's id
 * reconciliation already did that), a `token` appends to the TRAILING text
 * part if there is one. Only a streamed `text` UI part merges backward;
 * a `data-text` part never does.
 */
export function toMessageParts(message: PoseidonUIMessage): MessagePart[] {
  const out: MessagePart[] = [];
  for (const part of message.parts) {
    if (SKIPPED.has(part.type)) continue;
    if (part.type === "text") {
      const prev = out[out.length - 1];
      if (prev && prev.kind === "text") {
        const md = (prev.payload as { markdown: string }).markdown + part.text;
        out[out.length - 1] = { kind: "text", payload: { markdown: md } };
      } else {
        out.push({ kind: "text", payload: { markdown: part.text } });
      }
      continue;
    }
    if (part.type.startsWith("data-")) {
      out.push({ kind: part.type.slice(5), payload: (part as { data: unknown }).data });
    }
  }
  return out;
}

/** Persisted history -> UI message parts, the inverse for page load. */
export function fromMessageParts(parts: MessagePart[]): PoseidonUIMessage["parts"] {
  return parts.map((p) => {
    if (p.kind === "text") {
      return { type: "text", text: (p.payload as { markdown: string }).markdown, state: "done" as const };
    }
    if (p.kind === "tool_event") {
      const seq = (p.payload as { tool_seq: number }).tool_seq;
      return { type: "data-tool_event", id: `tool-${seq}`, data: p.payload } as PoseidonUIMessage["parts"][number];
    }
    return { type: `data-${p.kind}`, data: p.payload } as PoseidonUIMessage["parts"][number];
  });
}

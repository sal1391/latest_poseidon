import type { UIMessageChunk } from "ai";

export type EnvelopeFrame = {
  event: string;
  data: { turn_id: string; message_id: string; event_seq: number } & Record<string, unknown>;
};

export const TEXT_PART_ID = "text-1";
const ENVELOPE_KEYS = new Set(["turn_id", "message_id", "event_seq"]);

/** Port of frontend/src/api/sse.ts parseSseChunk; `id:` lines are ignored, the envelope carries event_seq. */
export function parseSseChunk(buffer: string): { frames: EnvelopeFrame[]; rest: string } {
  const frames: EnvelopeFrame[] = [];
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
    if (!event || dataLines.length === 0) continue;
    try {
      frames.push({ event, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      continue; // malformed frame: skip, never kill the stream
    }
  }
  return { frames, rest };
}

function payloadOf(frame: EnvelopeFrame): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(frame.data)) if (!ENVELOPE_KEYS.has(k)) out[k] = v;
  return out;
}

/**
 * Spec section 6, frame by frame. Pure: no I/O, no clock. The only state is
 * whether the streamed text part is open and whether a terminal frame was
 * seen, so `end()` can close a cut-off stream honestly.
 */
export function createFrameTranslator() {
  let textOpen = false;
  let finished = false;
  let turnId: string | undefined;

  const closeText = (): UIMessageChunk[] => {
    if (!textOpen) return [];
    textOpen = false;
    return [{ type: "text-end", id: TEXT_PART_ID }];
  };

  return {
    translate(frame: EnvelopeFrame): UIMessageChunk[] {
      turnId ??= frame.data.turn_id;
      const p = payloadOf(frame);
      switch (frame.event) {
        case "accepted":
          return [
            {
              type: "start",
              messageId: frame.data.message_id,
              messageMetadata: { turn_id: frame.data.turn_id, turn_index: p.turn_index },
            },
          ];
        case "phase":
          return [{ type: "data-phase", id: String(p.phase), data: p, transient: true }];
        case "tool":
          return [{ type: "data-tool_event", id: `tool-${p.tool_seq}`, data: p }];
        case "part":
          return [{ type: `data-${p.kind}`, data: p.payload } as UIMessageChunk];
        case "token": {
          const out: UIMessageChunk[] = [];
          if (!textOpen) {
            textOpen = true;
            out.push({ type: "text-start", id: TEXT_PART_ID });
          }
          out.push({ type: "text-delta", id: TEXT_PART_ID, delta: String(p.text) });
          return out;
        }
        case "done":
          finished = true;
          return [
            ...closeText(),
            { type: "finish", messageMetadata: { turn_id: turnId, status: "ok", usage: p.usage, title: p.title } },
          ];
        case "error":
          finished = true;
          return [
            ...closeText(),
            { type: "data-error", data: p } as UIMessageChunk,
            { type: "finish", messageMetadata: { turn_id: turnId, status: "error" } },
          ];
        default:
          return []; // unknown frame kinds are ignored, never fatal
      }
    },
    end(): UIMessageChunk[] {
      if (finished) return [];
      finished = true;
      return [...closeText(), { type: "error", errorText: "turn stream ended without a done or error frame" }];
    },
  };
}

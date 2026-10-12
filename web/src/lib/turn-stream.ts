import type { UIMessageChunk } from "ai";

export type EnvelopeFrame = {
  event: string;
  data: { turn_id: string; message_id: string; event_seq: number } & Record<string, unknown>;
};

/**
 * The id of the nth streamed-text run of a turn, 1-based (P16-R17). One turn
 * can hold several: a run is opened by a `token` frame and closed by the next
 * non-token frame, so `text-1` is the first run, `text-2` the one after the
 * part that interrupted it, and so on.
 */
export function textPartId(run: number): string {
  return `text-${run}`;
}

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
 * Spec section 6, frame by frame. Pure: no I/O, no clock. The state is the
 * open streamed-text run (if any), how many runs have been opened, and
 * whether a terminal frame was seen, so `end()` can close a cut-off stream
 * honestly.
 *
 * P16-R17 -- text runs are per-run ids, not one id per turn. A token run is
 * closed by the NEXT non-token frame of any kind, so the streamed text part
 * lands exactly where Python's `TurnTranscriptBuffer.fold_token` would leave
 * it ("append to the trailing part only if that part is text"). With a single
 * turn-wide id, `[token a][part table][token b]` folded `b` back into the
 * FIRST text part -- ahead of the table -- where Python produces
 * `[text a][table][text b]`. No producer can emit that today
 * (`orchestrator.py`'s single `sink.push_token` per turn), but the two folds
 * must agree by construction, not by luck.
 */
export function createFrameTranslator() {
  let openTextId: string | null = null;
  let textRuns = 0;
  let finished = false;
  let turnId: string | undefined;

  const closeText = (): UIMessageChunk[] => {
    if (openTextId === null) return [];
    const id = openTextId;
    openTextId = null;
    return [{ type: "text-end", id }];
  };

  return {
    translate(frame: EnvelopeFrame): UIMessageChunk[] {
      turnId ??= frame.data.turn_id;
      const p = payloadOf(frame);

      if (frame.event === "token") {
        const out: UIMessageChunk[] = [];
        if (openTextId === null) {
          openTextId = textPartId(++textRuns);
          out.push({ type: "text-start", id: openTextId });
        }
        out.push({ type: "text-delta", id: openTextId, delta: String(p.text) });
        return out;
      }

      // Every other frame kind ends the open run first -- including an unknown
      // one, which contributes nothing of its own but is still a part boundary.
      const out: UIMessageChunk[] = closeText();
      switch (frame.event) {
        case "accepted":
          out.push({
            type: "start",
            messageId: frame.data.message_id,
            messageMetadata: { turn_id: frame.data.turn_id, turn_index: p.turn_index },
          });
          break;
        case "phase":
          out.push({ type: "data-phase", id: String(p.phase), data: p, transient: true });
          break;
        case "tool":
          out.push({ type: "data-tool_event", id: `tool-${p.tool_seq}`, data: p });
          break;
        case "part":
          out.push({ type: `data-${p.kind}`, data: p.payload } as UIMessageChunk);
          break;
        case "done":
          finished = true;
          out.push({
            type: "finish",
            messageMetadata: { turn_id: turnId, status: "ok", usage: p.usage, title: p.title },
          });
          break;
        case "error":
          finished = true;
          out.push({ type: "data-error", data: p } as UIMessageChunk);
          out.push({ type: "finish", messageMetadata: { turn_id: turnId, status: "error" } });
          break;
        default:
          break; // unknown frame kinds are ignored, never fatal
      }
      return out;
    },
    end(): UIMessageChunk[] {
      if (finished) return [];
      finished = true;
      return [...closeText(), { type: "error", errorText: "turn stream ended without a done or error frame" }];
    },
  };
}

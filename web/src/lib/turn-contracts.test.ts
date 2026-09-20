import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { readUIMessageStream, type UIMessageChunk } from "ai";
import { describe, expect, it } from "vitest";
import { toMessageParts, type PoseidonUIMessage } from "./message-parts";
import { createFrameTranslator, type EnvelopeFrame } from "./turn-stream";

const CORPUS = join(__dirname, "..", "..", "..", "contracts", "turns");
type Golden = { scenario: string; frames: EnvelopeFrame[]; parts: unknown[]; terminal: EnvelopeFrame };

const goldens = readdirSync(CORPUS)
  .filter((f) => f.endsWith(".json") && f !== "opener.json")
  .map((f) => JSON.parse(readFileSync(join(CORPUS, f), "utf8")) as Golden);

async function replay(frames: EnvelopeFrame[]): Promise<PoseidonUIMessage> {
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

describe("contracts/turns replay through the translator and adapter", () => {
  expect(goldens.length).toBeGreaterThanOrEqual(5);
  it.each(goldens.map((g) => [g.scenario, g] as const))("%s: parts equal what Python persisted", async (_, g) => {
    const message = await replay(g.frames);
    const parts = toMessageParts(message);
    // P16-R15: Python never persists an `error` part -- the error is a stream event -- but the
    // translator still emits `data-error` so the live view can render the inline card.
    expect(parts.filter((p) => p.kind !== "error")).toEqual(g.parts);
    if (g.terminal.event === "error") {
      const errors = parts.filter((p) => p.kind === "error");
      expect(errors).toHaveLength(1);
      const payload = errors[0].payload as { code: unknown; message: unknown };
      expect(payload.code).toBe(g.terminal.data.code);
      expect(payload.message).toBe(g.terminal.data.message);
    } else {
      expect(parts.filter((p) => p.kind === "error")).toEqual([]);
    }
    expect(message.metadata?.status).toBe(g.terminal.event === "done" ? "ok" : "error");
    expect(message.metadata?.turn_id).toBe(g.frames[0].data.turn_id);
  });
});

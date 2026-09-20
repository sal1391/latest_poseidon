import { createUIMessageStream, createUIMessageStreamResponse, type UIMessageChunk } from "ai";
import { requireSales } from "../../../../lib/request-identity";
import { createFrameTranslator, parseSseChunk } from "../../../../lib/turn-stream";
import { streamTurn } from "../../../../lib/turns";

/**
 * One chat turn, re-streamed.
 *
 * Python runs the turn and emits the envelope frames this app has no business
 * reinventing; this handler is the adapter between that stream and the one the
 * AI SDK's `useChat` reads. It owns exactly three things -- the role check, the
 * idle deadline, and the frame-to-chunk translation -- and no chat logic at
 * all.
 *
 * **It must win over the `/api/*` rewrite.** `next.config.ts` rewrites
 * `/api/:path*` to the FastAPI backend, but `rewrites()`'s array form is
 * `afterFiles`, which runs only after the filesystem routes miss. A route
 * handler at this path is a filesystem route, so it answers first and the
 * rewrite never sees the request -- which is why Python's access log shows
 * only `/internal/v1/...` for a turn.
 */
export const dynamic = "force-dynamic";

/**
 * Spec section 10: no overall timeout, an idle timeout BETWEEN frames. A turn
 * legitimately runs for minutes (a research flow with several tool calls); a
 * wall-clock ceiling would cut healthy work in half, while a two-minute gap
 * with nothing on the wire means the upstream is wedged and the browser is
 * holding a connection that will never produce anything.
 */
const IDLE_MS = 120_000;

export async function POST(request: Request, { params }: { params: Promise<{ cid: string }> }) {
  const { cid } = await params;
  // Before the body is even read: `proxy.ts` already answered 403 to anything
  // lacking the role, so reaching here without it is a wiring fault, and
  // nothing should be sent to Python on its behalf.
  const identity = requireSales(request.headers);
  const { text, client_turn_key = null } = (await request.json()) as { text: string; client_turn_key?: string | null };

  const controller = new AbortController();
  let idle = setTimeout(() => controller.abort(), IDLE_MS);
  const touch = () => {
    clearTimeout(idle);
    idle = setTimeout(() => controller.abort(), IDLE_MS);
  };

  // The timer is armed before the call, so every way out has to disarm it.
  // The two normal exits do it below; a `streamTurn` that throws outright -- an
  // unset token, a refused connection -- would otherwise leave a live
  // two-minute timer behind for every request a misconfigured deployment
  // receives.
  let upstream: Response;
  try {
    upstream = await streamTurn(cid, { text, client_turn_key }, identity, controller.signal);
  } catch (err) {
    clearTimeout(idle);
    throw err;
  }
  if (!upstream.ok || !upstream.body) {
    clearTimeout(idle);
    // Status AND body: Python's refusals are RFC-7807 problem details naming
    // the conversation, the limit, or the missing `CHAT_MODE=live`. Flattening
    // them into a bare status would lose the only part a reader can act on.
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  }

  const body = upstream.body;
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      const translator = createFrameTranslator();
      const reader = body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          touch();
          buffer += decoder.decode(value, { stream: true });
          const { frames, rest } = parseSseChunk(buffer);
          buffer = rest;
          for (const frame of frames) for (const chunk of translator.translate(frame)) writer.write(chunk);
        }
        // `end()` is empty after a terminal frame and closes an open text run
        // plus emits an `error` chunk otherwise -- which is what makes a turn
        // that died mid-stream visible in the page rather than merely short.
        for (const chunk of translator.end()) writer.write(chunk);
      } catch (err) {
        // Written here, not left to `createUIMessageStream`'s own handler,
        // whose default `onError` deliberately masks every message as "An
        // error occurred." A transport failure on this seam is ours, not a
        // model provider's, and naming it is the difference between a
        // debuggable dev loop and a blank card.
        writer.write({ type: "error", errorText: `turn stream interrupted: ${(err as Error).message}` } as UIMessageChunk);
      } finally {
        clearTimeout(idle);
      }
    },
  });
  return createUIMessageStreamResponse({ stream });
}

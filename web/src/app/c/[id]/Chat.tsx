"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef } from "react";
import { toMessageParts, type PoseidonUIMessage } from "../../../lib/message-parts";
import { PartRenderer } from "../../../ui/message-parts/registry";
import { Composer } from "../../../ui/shell/Composer";

/** client_turn_key per user message, reused if the SDK retries the same message (Python dedups on it). */
const turnKeys = new Map<string, string>();

/** How close to the end (px) still counts as "reading the latest". */
const NEAR_BOTTOM_PX = 48;

/** The text a user message carries, folded back out of its parts. */
function userText(message: PoseidonUIMessage): string {
  return message.parts
    .filter((p) => p.type === "text")
    .map((p) => (p as { text: string }).text)
    .join("");
}

export function Chat({ conversationId, initialMessages }: { conversationId: string; initialMessages: PoseidonUIMessage[] }) {
  const transport = useMemo(
    () =>
      new DefaultChatTransport<PoseidonUIMessage>({
        api: `/api/chat/${conversationId}`,
        // The route takes Python's send body (`text` + `client_turn_key`), not
        // the SDK's default `{ messages }`: the conversation lives in Postgres
        // and Python reads its own history, so shipping the whole transcript
        // back would be both wasted bytes and a second, disagreeing source of
        // truth for what was said.
        prepareSendMessagesRequest: ({ messages }) => {
          const last = messages[messages.length - 1];
          const text = userText(last);
          if (!turnKeys.has(last.id)) turnKeys.set(last.id, crypto.randomUUID());
          return { body: { text, client_turn_key: turnKeys.get(last.id) } };
        },
      }),
    [conversationId],
  );
  const router = useRouter();
  // The sidebar list and the header title are rendered by the root layout,
  // and a layout does not re-render on a soft navigation. Python retitles a
  // conversation on its first turn and bumps updated_at on every turn, so a
  // refresh on finish is what keeps the list and the title current. useChat
  // seeds its messages once per id and ignores later initialMessages, so the
  // refresh cannot reset the thread.
  const { messages, sendMessage, status, error } = useChat<PoseidonUIMessage>({
    id: conversationId,
    messages: initialMessages,
    transport,
    onFinish: () => router.refresh(),
  });
  /**
   * In flight, which is NOT `status !== "ready"`: the fourth status is
   * `"error"`, and treating it as busy would leave the composer and every chip
   * disabled after a failed turn, with reloading the page as the only way out.
   * `sendMessage` clears the error and moves back to `"submitted"` itself
   * (`AbstractChat.makeRequest`), so retrying from an error state is exactly
   * what should stay possible.
   */
  const busy = status === "submitted" || status === "streaming";

  // The thread scrolls inside its own column (spec 3.2), so the browser no
  // longer keeps the newest message in view for free. Follow the end of the
  // thread only while the reader is already there: someone who scrolled up
  // to re-read an earlier answer stays put while tokens stream in. `stick`
  // is re-derived on every scroll and read after every render that changes
  // the messages or the error line; a send always re-engages it.
  const threadRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // Cheap insurance: if Next reuses this instance between /c/A and /c/B,
  // a `false` left over from A would open B at A's offset instead of its end.
  useEffect(() => {
    stick.current = true;
  }, [conversationId]);
  function onScroll() {
    const el = threadRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
  }
  useEffect(() => {
    const el = threadRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, error]);

  function send(text: string) {
    if (busy || text.trim() === "") return;
    stick.current = true;
    void sendMessage({ text });
  }

  return (
    <>
      <div ref={threadRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        <ol className="mx-auto flex w-full max-w-[720px] flex-col gap-4 px-4 py-6">
          {messages.map((m) =>
            m.role === "user" ? (
              <li key={m.id} className="max-w-[85%] self-end whitespace-pre-wrap break-words rounded-card bg-surface-raised px-4 py-3">
                {userText(m)}
              </li>
            ) : (
              <li key={m.id} className="flex flex-col gap-3 py-1">
                {/* In order, every part: P16-R17 means one assistant message can
                    hold SEVERAL text parts (a token run is closed by the next
                    non-token frame), so there is no such thing as "the" text part
                    to single out. */}
                {toMessageParts(m).map((part, i) => (
                  <PartRenderer key={i} part={part} disabled={busy} onChipSelect={(_id, sendText) => send(sendText)} />
                ))}
              </li>
            ),
          )}
          {/* The translator's `end()` emits an `error` chunk on a stream that died
              without a terminal frame, and `useChat` surfaces it here. Rendering it
              is what makes a truncated turn visible rather than merely short. */}
          {error ? <li className="text-[13px] text-negative">{error.message}</li> : null}
        </ol>
      </div>
      <div className="px-4">
        <Composer disabled={busy} onSend={send} />
      </div>
    </>
  );
}

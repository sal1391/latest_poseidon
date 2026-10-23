"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useEffect, useMemo, useRef } from "react";
import { toMessageParts, type PoseidonUIMessage } from "../../../lib/message-parts";
import { PartRenderer } from "../../../ui/message-parts/registry";
import { Composer } from "../../../ui/shell/Composer";

/** client_turn_key per user message, reused if the SDK retries the same message (Python dedups on it). */
const turnKeys = new Map<string, string>();

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
  const { messages, sendMessage, status, error } = useChat<PoseidonUIMessage>({ id: conversationId, messages: initialMessages, transport });
  /**
   * In flight, which is NOT `status !== "ready"`: the fourth status is
   * `"error"`, and treating it as busy would leave the composer and every chip
   * disabled after a failed turn, with reloading the page as the only way out.
   * `sendMessage` clears the error and moves back to `"submitted"` itself
   * (`AbstractChat.makeRequest`), so retrying from an error state is exactly
   * what should stay possible.
   */
  const busy = status === "submitted" || status === "streaming";

  // The thread scrolls inside its own column now (spec 3.2), so the page no
  // longer grows with the conversation and the browser no longer keeps the
  // newest message in view for free. jsdom has no scrollIntoView; the
  // optional call keeps the component testable there.
  const endRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages]);

  function send(text: string) {
    if (busy || text.trim() === "") return;
    void sendMessage({ text });
  }

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto">
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
          <li ref={endRef} aria-hidden="true" />
        </ol>
      </div>
      <div className="px-4">
        <Composer disabled={busy} onSend={send} />
      </div>
    </>
  );
}

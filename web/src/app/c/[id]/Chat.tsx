"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useMemo, useState } from "react";
import { toMessageParts, type PoseidonUIMessage } from "../../../lib/message-parts";
import { PartRenderer } from "../../../ui/message-parts/registry";

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
  const [draft, setDraft] = useState("");
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

  function send(text: string) {
    if (busy || text.trim() === "") return;
    setDraft("");
    void sendMessage({ text });
  }

  return (
    <div className="mt-4 space-y-4">
      <ol className="space-y-4">
        {messages.map((m) => (
          <li key={m.id} className={m.role === "user" ? "text-right" : ""}>
            <span className="text-xs uppercase opacity-60">{m.role}</span>
            {m.role === "user" ? (
              <p>{userText(m)}</p>
            ) : (
              // In order, every part: P16-R17 means one assistant message can
              // hold SEVERAL text parts (a token run is closed by the next
              // non-token frame), so there is no such thing as "the" text part
              // to single out.
              toMessageParts(m).map((part, i) => (
                <PartRenderer key={i} part={part} disabled={busy} onChipSelect={(_id, sendText) => send(sendText)} />
              ))
            )}
          </li>
        ))}
      </ol>
      {/* The translator's `end()` emits an `error` chunk on a stream that died
          without a terminal frame, and `useChat` surfaces it here. Rendering it
          is what makes a truncated turn visible rather than merely short. */}
      {error ? <p className="text-red-600">{error.message}</p> : null}
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
        <input className="flex-1 rounded border px-2 py-1" aria-label="Message Poseidon" placeholder="Message Poseidon…" value={draft} disabled={busy} onChange={(e) => setDraft(e.target.value)} />
        <button type="submit" className="rounded border px-3 py-1" disabled={busy || draft.trim() === ""}>Send</button>
      </form>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { useChatStore } from "../../state/chatStore";
import { PartRenderer } from "../../ui/message-parts/registry";
import { Feedback } from "../../ui/primitives/Feedback";
import { Sidebar } from "../conversations/Sidebar";
import { Composer } from "./Composer";

/** Flow-chip entry stub: seeds the composer. Real dispatch arrives in Phase 8. */
const chipTemplate = (label: string) => `Run the ${label} flow for `;

export default function ChatScreen() {
  const activeId = useChatStore((s) => s.activeId);
  const messagesByConv = useChatStore((s) => s.messages);
  const streamingByConv = useChatStore((s) => s.streamingByConv);
  const feedback = useChatStore((s) => s.feedback);
  const bootstrap = useChatStore((s) => s.bootstrap);
  const newConversation = useChatStore((s) => s.newConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const submitFeedback = useChatStore((s) => s.submitFeedback);

  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const booted = useRef<Promise<void> | null>(null);

  useEffect(() => {
    // Ref-guarded so StrictMode's double-mount cannot open two conversations.
    // A failed bootstrap leaves the shell live; the next send creates one.
    booted.current ??= bootstrap().catch(() => undefined);
  }, [bootstrap]);

  const messages = activeId ? (messagesByConv[activeId] ?? []) : [];
  const streaming = activeId ? streamingByConv[activeId] === true : false;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messagesByConv, activeId]);

  const insert = useCallback((text: string) => {
    setDraft(text);
    inputRef.current?.focus();
  }, []);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (trimmed === "") return;
      setDraft("");
      void (async () => {
        // The composer is live before bootstrap settles: wait for the
        // conversation it opens rather than racing it with a second one.
        await booted.current;
        const cid = useChatStore.getState().activeId ?? (await newConversation());
        await sendMessage(cid, trimmed);
      })().catch(() => undefined); // stream failures surface as an error part
    },
    [newConversation, sendMessage],
  );

  return (
    <>
      <Sidebar />
      <main className="chat-column">
        <div className="thread" aria-live="polite">
          {messages.map((message) => (
            <article
              key={message.id}
              className={message.role === "user" ? "msg-user" : "msg-assistant"}
            >
              {message.parts.map((part, index) => (
                <PartRenderer
                  key={`${message.id}:${index}`}
                  part={part}
                  onChipSelect={(_id, label) => insert(chipTemplate(label))}
                />
              ))}
              {message.role === "assistant" ? (
                <Feedback
                  verdict={feedback[message.id]?.verdict}
                  onSubmit={(verdict, comment) => {
                    void submitFeedback(message.id, verdict, comment).catch(() => undefined);
                  }}
                />
              ) : null}
            </article>
          ))}
          <div ref={endRef} />
        </div>
        <Composer
          value={draft}
          onChange={setDraft}
          onInsert={insert}
          onSubmit={send}
          disabled={streaming}
          inputRef={inputRef}
        />
      </main>
    </>
  );
}

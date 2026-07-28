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
  // Which conversation the in-flight send belongs to. `undefined` = idle;
  // `null` = launched before bootstrap settled, so it has no conversation yet.
  // Keyed rather than boolean so a send in A cannot disable the composer in B.
  const [sendingFor, setSendingFor] = useState<string | null | undefined>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // `bootstrap` is re-entrant in the store, so StrictMode's double effect joins
    // one run. A failed bootstrap leaves the shell live; the next send retries.
    void bootstrap().catch(() => undefined);
  }, [bootstrap]);

  const messages = activeId ? (messagesByConv[activeId] ?? []) : [];
  const streaming = activeId ? streamingByConv[activeId] === true : false;
  // A pre-bootstrap send (`null`) has no home yet, so it blocks everywhere until
  // it settles; once a send is tied to a conversation it blocks only that one.
  const blocked = sendingFor !== undefined && (sendingFor === null || sendingFor === activeId);

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
      if (trimmed === "" || sendingFor !== undefined) return;
      setDraft("");
      // Captured before the first await: until a conversation exists `streaming`
      // is false, so this is what disables the composer for the very first
      // message. `null` here means bootstrap has not landed yet.
      setSendingFor(useChatStore.getState().activeId);
      void (async () => {
        // The composer is live before bootstrap settles. With no conversation
        // open yet, join the store's in-flight bootstrap rather than racing it;
        // once one is open this is a no-op, so a send never re-lists and never
        // yanks the user off the conversation they are reading.
        if (useChatStore.getState().activeId === null) await bootstrap();
        const cid = useChatStore.getState().activeId ?? (await newConversation());
        await sendMessage(cid, trimmed);
      })()
        .catch(() => undefined) // stream failures surface as an error part
        .finally(() => setSendingFor(undefined));
    },
    [bootstrap, newConversation, sendMessage, sendingFor],
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
          disabled={blocked || streaming}
          inputRef={inputRef}
        />
      </main>
    </>
  );
}

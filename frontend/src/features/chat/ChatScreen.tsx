import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useChatStore } from "../../state/chatStore";
import { PartRenderer } from "../../ui/message-parts/registry";
import { Feedback } from "../../ui/primitives/Feedback";
import { Sidebar } from "../conversations/Sidebar";
import { isTurnBackedAssistantMessage } from "../feedback/turnBacked";
import { Composer } from "./Composer";

// Phase 12 Task 4 (a11y carry-list): visually-hidden but screen-reader
// reachable -- the standard clip-rect pattern, inlined rather than a CSS
// class since no stylesheet is a sanctioned file for this task.
const statusRegionStyle = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
} as const;

export default function ChatScreen() {
  const activeId = useChatStore((s) => s.activeId);
  const messagesByConv = useChatStore((s) => s.messages);
  const messagesNextCursorByConv = useChatStore((s) => s.messagesNextCursor);
  const loadingEarlierByConv = useChatStore((s) => s.loadingEarlierMessages);
  const streamingByConv = useChatStore((s) => s.streamingByConv);
  const feedback = useChatStore((s) => s.feedback);
  const openerIdByConv = useChatStore((s) => s.openerIdByConv);
  const bootstrap = useChatStore((s) => s.bootstrap);
  const newConversation = useChatStore((s) => s.newConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const loadEarlierMessages = useChatStore((s) => s.loadEarlierMessages);
  const submitFeedback = useChatStore((s) => s.submitFeedback);

  const [draft, setDraft] = useState("");
  // Which conversation the in-flight send belongs to. `undefined` = idle;
  // `null` = launched before bootstrap settled, so it has no conversation yet.
  // Keyed rather than boolean so a send in A cannot disable the composer in B.
  const [sendingFor, setSendingFor] = useState<string | null | undefined>(undefined);
  // True when the last bootstrap attempt couldn't reach the backend, so the
  // shell would otherwise sit there looking normal while doing nothing.
  const [connectionError, setConnectionError] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  // Phase 12 Task 4 (page-order amendment, 2026-08-04): the scrollable
  // thread itself, and the two refs "Load earlier messages" coordinates
  // through -- see handleLoadEarlier/the two effects below for how they
  // cooperate to keep the user's current view stable while older messages
  // are prepended ABOVE it, instead of either the default "jump to newest"
  // behavior (the effect below this one) or a visual jump caused by new
  // content appearing above the current scroll position.
  const threadRef = useRef<HTMLDivElement>(null);
  const suppressAutoScrollRef = useRef(false);
  // Anchored on a SPECIFIC message's own DOM node (via `data-message-id`
  // below), not an aggregate `scrollHeight` delta -- see handleLoadEarlier's
  // own comment for why: `scrollHeight` alone is not a reliable proxy for
  // "how much content was added above the anchor" once other, unrelated
  // layout changes can land anywhere in the list at the same time.
  const pendingScrollAnchorRef = useRef<{ messageId: string; topBefore: number } | null>(null);

  const runBootstrap = useCallback(
    () => bootstrap().then(() => setConnectionError(false)).catch(() => setConnectionError(true)),
    [bootstrap],
  );

  useEffect(() => {
    // `bootstrap` is re-entrant in the store, so StrictMode's double effect joins
    // one run. A failed bootstrap surfaces the retry banner below instead of
    // leaving a silently dead shell.
    void runBootstrap();
  }, [runBootstrap]);

  const retry = useCallback(() => {
    setConnectionError(false);
    void runBootstrap();
  }, [runBootstrap]);

  const messages = activeId ? (messagesByConv[activeId] ?? []) : [];
  const streaming = activeId ? streamingByConv[activeId] === true : false;
  // "Load earlier messages" renders exactly when there is an older page to
  // fetch -- mirrors Sidebar's own `nextCursor !== null` contract for its
  // load-more control.
  const messagesNextCursor = activeId ? (messagesNextCursorByConv[activeId] ?? null) : null;
  const loadingEarlier = activeId ? loadingEarlierByConv[activeId] === true : false;

  // Phase 12 Task 4 (a11y carry-list, verbatim): the thread used to carry
  // `aria-live="polite"` directly, so every streamed token re-announced the
  // whole growing answer -- see this file's own `.thread` div below, which
  // no longer does. This status region announces the turn's LIFECYCLE
  // instead (thinking -> done), derived purely from `streaming`'s own
  // false->true / true->false edges, so it fires exactly twice per turn
  // regardless of how many token/tool/part frames land in between. Known,
  // accepted boundary: switching `activeId` mid-stream can also flip this
  // boolean (a different conversation's own flag), which is not itself a
  // turn edge -- out of this task's scope (no reported gap names it).
  const [turnStatus, setTurnStatus] = useState("");
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    if (streaming && !wasStreamingRef.current) {
      setTurnStatus("Poseidon is thinking...");
    } else if (!streaming && wasStreamingRef.current) {
      setTurnStatus("Poseidon has replied.");
    }
    wasStreamingRef.current = streaming;
  }, [streaming]);

  // The opener (the first message of every conversation) carries no linked
  // turn and 422s if feedback is attempted on it -- see turnBacked.ts's own
  // docstring for why this is the safe positional signal to gate on.
  const openerId = activeId ? openerIdByConv[activeId] : undefined;
  // A pre-bootstrap send (`null`) has no home yet, so it blocks everywhere until
  // it settles; once a send is tied to a conversation it blocks only that one.
  const blocked = sendingFor !== undefined && (sendingFor === null || sendingFor === activeId);

  useEffect(() => {
    // Skipped for exactly the one render that just prepended an older page
    // (handleLoadEarlier below sets this synchronously before the fetch
    // that causes it) -- jumping to the newest message on every messages
    // change is right for a new turn arriving, but wrong here: it would
    // undo the scroll-anchor restoration the layout effect below performs
    // for a "Load earlier" prepend.
    if (suppressAutoScrollRef.current) {
      suppressAutoScrollRef.current = false;
      return;
    }
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messagesByConv, activeId]);

  // Restores the user's visual scroll position after older messages are
  // prepended above it: a `useLayoutEffect` (not `useEffect`) so it runs
  // BEFORE the browser paints the newly taller thread, and before the
  // sibling effect above would otherwise be free to scroll it. Re-measures
  // the SAME anchor message's own `getBoundingClientRect().top` (found via
  // its `data-message-id`, set on every `<article>` below) and nudges
  // `scrollTop` by exactly however far THAT element moved -- deliberately
  // NOT inferred from a `scrollHeight` delta (an earlier version of this
  // effect did that, live-verified via Playwright against a real seeded
  // long conversation to be WRONG here specifically: the very click that
  // first reveals a conversation's true opener also retroactively makes
  // every already-loaded assistant message eligible for a Feedback row for
  // the first time -- turnBacked.ts's own "openerId unknown -> withhold"
  // fail-closed default, from earlier in this same task, means thumbs were
  // withheld everywhere in a long conversation until this exact moment --
  // adding height throughout the ALREADY-loaded list, not only above the
  // anchor, so "total height added" and "height added above the anchor"
  // are two different numbers on that specific click). Measuring the
  // anchor element's own before/after position directly is correct
  // regardless of WHY or WHERE other height changed.
  useLayoutEffect(() => {
    const el = threadRef.current;
    const anchor = pendingScrollAnchorRef.current;
    if (el && anchor) {
      const anchorEl = el.querySelector(`[data-message-id="${anchor.messageId}"]`);
      if (anchorEl) {
        el.scrollTop += anchorEl.getBoundingClientRect().top - anchor.topBefore;
      }
      pendingScrollAnchorRef.current = null;
    }
  }, [messagesByConv]);

  const handleLoadEarlier = useCallback(() => {
    if (!activeId) return;
    const el = threadRef.current;
    const currentFirst = (messagesByConv[activeId] ?? [])[0];
    const anchorEl = el && currentFirst
      ? el.querySelector(`[data-message-id="${currentFirst.id}"]`)
      : null;
    if (anchorEl) {
      suppressAutoScrollRef.current = true;
      pendingScrollAnchorRef.current = {
        messageId: currentFirst.id,
        topBefore: anchorEl.getBoundingClientRect().top,
      };
    }
    // Review fix round 1, Important #2: both refs above are armed
    // synchronously, BEFORE `loadEarlierMessages`'s own fetch even starts --
    // correct for the success path, where the `messages` state change that
    // follows is exactly what the two effects above are waiting to consume
    // and clear. On FAILURE (a rejected `ApiError`, e.g. a 5xx or network
    // drop), `messages` never changes, so neither effect ever re-fires to
    // clear them -- both refs would stay armed indefinitely, silently
    // corrupting the NEXT, wholly unrelated messages change (e.g. the next
    // chat turn arriving): its normal scroll-to-newest would be suppressed
    // once, and a stale, long-outdated anchor measurement could get applied
    // to it. Clearing both here, on failure specifically, closes that leak.
    void loadEarlierMessages(activeId).catch(() => {
      suppressAutoScrollRef.current = false;
      pendingScrollAnchorRef.current = null;
    });
  }, [activeId, loadEarlierMessages, messagesByConv]);

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
        if (useChatStore.getState().activeId === null) {
          await bootstrap().catch((err) => {
            setConnectionError(true);
            throw err;
          });
        }
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
        {connectionError ? (
          <div className="error-card" role="alert">
            {"Can't reach the Poseidon backend. Check that it's running (see infra/runbooks/local.md), then retry. "}
            <button type="button" onClick={retry}>
              Retry
            </button>
          </div>
        ) : null}
        <div role="status" aria-live="polite" aria-atomic="true" style={statusRegionStyle}>
          {turnStatus}
        </div>
        <div className="thread" ref={threadRef}>
          {messagesNextCursor !== null ? (
            <button
              type="button"
              className="load-earlier"
              disabled={loadingEarlier}
              aria-busy={loadingEarlier}
              onClick={handleLoadEarlier}
            >
              {loadingEarlier ? "Loading..." : "Load earlier messages"}
            </button>
          ) : null}
          {messages.map((message) => (
            <article
              key={message.id}
              data-message-id={message.id}
              className={message.role === "user" ? "msg-user" : "msg-assistant"}
            >
              {message.parts.map((part, index) => (
                <PartRenderer
                  key={`${message.id}:${index}`}
                  part={part}
                  onChipSelect={(_id, label) => send(label)}
                  disabled={blocked || streaming}
                />
              ))}
              {isTurnBackedAssistantMessage(message, openerId, messagesNextCursor !== null) ? (
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

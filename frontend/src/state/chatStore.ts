import { create } from "zustand";
import type { Conversation, Message, MessagePart, SseEvent } from "../api/types";
import * as api from "./../api/client";
import { StreamError, streamTurn } from "../api/sse";
import { isTurnBackedAssistantMessage } from "../features/feedback/turnBacked";

export function applyEventTo(messages: Message[], e: SseEvent): Message[] {
  const { message_id, event_seq } = e.data;
  const msgs = messages.map((m) => ({ ...m, parts: [...m.parts] }));
  let msg = msgs.find((m) => m.id === message_id);
  if (e.name === "accepted") {
    if (msg) return messages;
    msgs.push({ id: message_id, role: "assistant", parts: [], lastSeq: event_seq });
    return msgs;
  }
  if (!msg) {
    // Replay-safe: an event for an unseen message creates it.
    msg = { id: message_id, role: "assistant", parts: [], lastSeq: 0 };
    msgs.push(msg);
  }
  if (event_seq <= (msg.lastSeq ?? 0)) return messages; // duplicate delivery
  msg.lastSeq = event_seq;
  switch (e.name) {
    case "tool": {
      const { turn_id: _t, message_id: _m, event_seq: _s, ...payload } = e.data;
      const i = msg.parts.findIndex(
        (p) => p.kind === "tool_event" &&
          (p.payload as { tool_seq: number }).tool_seq === payload.tool_seq,
      );
      const part = { kind: "tool_event", payload };
      if (i >= 0) msg.parts[i] = part;
      else msg.parts.push(part);
      return msgs;
    }
    case "token": {
      const tail = msg.parts[msg.parts.length - 1];
      if (tail?.kind === "text") {
        msg.parts[msg.parts.length - 1] = {
          kind: "text",
          payload: { markdown: (tail.payload as { markdown: string }).markdown + e.data.text },
        };
      } else {
        msg.parts.push({ kind: "text", payload: { markdown: e.data.text } });
      }
      return msgs;
    }
    case "part": {
      const { turn_id: _t, message_id: _m, event_seq: _s, ...part } = e.data;
      msg.parts.push(part as MessagePart);
      return msgs;
    }
    case "error": {
      const { turn_id: _t, message_id: _m, event_seq: _s, ...payload } = e.data;
      msg.parts.push({ kind: "error", payload });
      return msgs;
    }
    default:
      return msgs; // done/phase: lastSeq already advanced
  }
}

export interface ChatState {
  conversations: Conversation[];
  // The cursor for the NEXT page of conversations, or `null` when the list
  // is exhausted (also true before the first successful list, same as
  // "nothing more to load"). `Sidebar`'s load-more control renders exactly
  // when this is non-null.
  conversationsNextCursor: string | null;
  // Fix round 1 (review finding I-1): `loadMoreConversations` appends off a
  // cursor read before its own `await`, so two overlapping calls (a
  // double-click) would both read the SAME cursor and both append the SAME
  // page -- unlike `sendMessage`'s `streamingByConv` guard, which this
  // mirrors, there was no flag stopping the second one. `false` outside any
  // in-flight load.
  loadingMoreConversations: boolean;
  activeId: string | null;
  messages: Record<string, Message[]>;
  // Phase 12 Task 4 (page-order amendment, 2026-08-04): the cursor for the
  // NEXT (i.e. OLDER, since the backend flip -- history.py's own amendment
  // docstring) page of a conversation's messages, or `null` when there is
  // nothing further back to load. `ChatScreen`'s "Load earlier messages"
  // control renders exactly when this is non-null, mirroring
  // `conversationsNextCursor`/Sidebar's own load-more contract.
  messagesNextCursor: Record<string, string | null>;
  // In-flight guard for `loadEarlierMessages`, keyed by conversation id (the
  // P10 lesson, review finding I-1, that `loadMoreConversations` below
  // already carries -- see that action's own comment) -- keyed rather than a
  // single flag since a load in ONE conversation must not block another's.
  loadingEarlierMessages: Record<string, boolean>;
  streamingByConv: Record<string, boolean>;
  feedback: Record<string, { verdict: "up" | "down"; comment?: string }>;
  // Phase 12 Task 2: the opener (the conversation's first-ever message --
  // see turnBacked.ts's own docstring for why this positional signal is
  // safe absent a backend turnId field) is the one assistant message that
  // must never be offered feedback. Phase 12 Task 4's page-order amendment
  // means a conversation's FIRST-LOADED page is no longer necessarily its
  // earliest (a >`limit`-message conversation's first page is its LATEST
  // window) -- so this is set at every one of the four places a page of
  // messages loads (bootstrap's create branch, newConversation,
  // openConversation, loadEarlierMessages) ONLY when that page's own
  // `next_cursor` is `null`, i.e. only when that page's own first item is
  // genuinely known to be the conversation's first-ever message. A cid
  // present in `messages` with no entry here yet means the true opener has
  // not been walked back to -- `isTurnBackedAssistantMessage` (turnBacked.ts)
  // covers that state too, via `messagesNextCursor[cid] !== null`: a
  // non-null cursor proves the opener isn't among the currently-loaded
  // messages, so every one of them is safe to offer thumbs on regardless.
  openerIdByConv: Record<string, string>;
  bootstrap: () => Promise<void>;
  newConversation: () => Promise<string>;
  openConversation: (cid: string) => Promise<void>;
  // "Load earlier messages": fetches the next (older) page via
  // `messagesNextCursor[cid]` and PREPENDS it to `messages[cid]` -- the
  // fetched page is already oldest-to-newest within itself (the wire
  // contract get_messages still honors -- history.py's own amendment
  // docstring) and is chronologically older than everything already
  // loaded, so no reordering is needed beyond prepending it whole. No-ops
  // when there is nothing more to load or a load is already in flight for
  // this cid (mirrors `loadMoreConversations`'s own guard below).
  loadEarlierMessages: (cid: string) => Promise<void>;
  // Fires one GET per turn-backed assistant message currently loaded for
  // `cid`, hydrating `feedback` with whatever verdict is already recorded.
  // Best-effort: a 404 (nothing recorded, or the route's other 404 reason --
  // task-2-brief's own note that hydration treats both alike) or any other
  // per-message failure is swallowed, never thrown -- called from
  // `openConversation` below, so a hydration hiccup must not block a
  // conversation from opening.
  hydrateFeedback: (cid: string) => Promise<void>;
  // clientTurnKey: omitted for a brand-new logical send (one is minted);
  // passed explicitly to RETRY that same send with the backend's own
  // (user_sub, client_turn_key) idempotency short-circuit intact -- see
  // this function's own implementation comment below.
  sendMessage: (cid: string, text: string, clientTurnKey?: string) => Promise<void>;
  loadMoreConversations: () => Promise<void>;
  applyEvent: (cid: string, e: SseEvent) => void;
  submitFeedback: (mid: string, verdict: "up" | "down", comment?: string) => Promise<void>;
}

/** Shared by every concurrent caller so one mount can only open one conversation. */
let bootstrapInFlight: Promise<void> | null = null;

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  conversationsNextCursor: null,
  loadingMoreConversations: false,
  activeId: null,
  messages: {},
  messagesNextCursor: {},
  loadingEarlierMessages: {},
  streamingByConv: {},
  feedback: {},
  openerIdByConv: {},

  bootstrap: () => {
    // Re-entrant: StrictMode's double effect, or a send issued before the first
    // list request lands, joins the in-flight run instead of starting a second.
    bootstrapInFlight ??= (async () => {
      const page = await api.listConversations();
      if (page.items.length === 0) {
        const { conversation, opener } = await api.createConversation();
        set((s) => ({
          conversations: [conversation],
          conversationsNextCursor: null,
          activeId: conversation.id,
          messages: { ...s.messages, [conversation.id]: [opener] },
          // A brand-new conversation's only message IS the opener -- there
          // is nothing older to page in, so next_cursor is null from the
          // start and openerIdByConv is set unconditionally (contrast
          // openConversation/loadEarlierMessages below, which have to check).
          messagesNextCursor: { ...s.messagesNextCursor, [conversation.id]: null },
          openerIdByConv: { ...s.openerIdByConv, [conversation.id]: opener.id },
        }));
        return;
      }
      set({ conversations: page.items, conversationsNextCursor: page.next_cursor });
      await get().openConversation(page.items[0].id);
    })().finally(() => {
      bootstrapInFlight = null; // a later mount (or a retry after failure) starts fresh
    });
    return bootstrapInFlight;
  },

  newConversation: async () => {
    const { conversation, opener } = await api.createConversation();
    set((s) => ({
      // Newest first, matching the order `GET /api/conversations` returns and the
      // position `bootstrap` opens.
      conversations: [conversation, ...s.conversations],
      activeId: conversation.id,
      messages: { ...s.messages, [conversation.id]: [opener] },
      messagesNextCursor: { ...s.messagesNextCursor, [conversation.id]: null },
      openerIdByConv: { ...s.openerIdByConv, [conversation.id]: opener.id },
    }));
    return conversation.id;
  },

  openConversation: async (cid) => {
    set({ activeId: cid });
    const page = await api.getMessages(cid);
    set((s) => ({
      messages: { ...s.messages, [cid]: page.items },
      messagesNextCursor: { ...s.messagesNextCursor, [cid]: page.next_cursor },
      // Only when this first page is the WHOLE history (no next_cursor --
      // see the interface's own doc comment above for why a >limit-message
      // conversation's first page cannot be trusted to start with the
      // opener since the page-order amendment).
      openerIdByConv:
        page.items.length > 0 && page.next_cursor === null
          ? { ...s.openerIdByConv, [cid]: page.items[0].id }
          : s.openerIdByConv,
    }));
    // Fire-and-forget (final-review finding, Phase 12 whole-phase review):
    // every per-message request inside hydrateFeedback already swallows its
    // own failure, so this can never reject -- but AWAITING it here used to
    // put the entire fan-out (up to ~100 concurrent GETs, one per
    // turn-backed assistant message on a full `limit`-message page) on
    // `bootstrap()`'s own critical path, blocking the UI on best-effort
    // enrichment of an already-rendered conversation. `void`-ing it means a
    // slow or large hydration fan-out no longer delays opening a
    // conversation; tests that used to rely on this promise settling before
    // asserting hydrated `feedback` state now need their own `waitFor`.
    void get().hydrateFeedback(cid);
  },

  hydrateFeedback: async (cid) => {
    const messages = get().messages[cid] ?? [];
    const openerId = get().openerIdByConv[cid];
    // `messagesNextCursor[cid] !== null` mirrors ChatScreen's own render-
    // gating call: a non-null cursor means the true opener provably is NOT
    // among the currently-loaded messages, so hydration is safe to fire for
    // all of them even before the opener has been walked back to (see
    // turnBacked.ts's own docstring, state 2).
    const hasOlderPages = get().messagesNextCursor[cid] !== null;
    const targets = messages.filter((m) =>
      isTurnBackedAssistantMessage(m, openerId, hasOlderPages));
    await Promise.all(
      targets.map(async (m) => {
        try {
          const result = await api.getFeedback(m.id);
          set((s) => ({
            feedback: {
              ...s.feedback,
              [m.id]: { verdict: result.verdict, comment: result.comment ?? undefined },
            },
          }));
        } catch (err) {
          // A 404 (nothing recorded yet, the common case) is expected, not
          // exceptional -- task-2-brief's own "treat any 404 as simply
          // nothing to hydrate," so it stays silent. Any other failure
          // (e.g. a real 500 or network error) is still swallowed -- this
          // fan-out remains best-effort and must never block or surface an
          // error for the rest of it -- but is now logged: hydration is the
          // ONLY path by which a persisted verdict ever reaches the UI, so a
          // silent non-404 failure here would otherwise make a user's real
          // recorded verdict vanish with zero diagnostics.
          const status = err instanceof api.ApiError ? err.status : undefined;
          if (status !== 404) {
            console.warn(`hydrateFeedback: GET feedback failed for message ${m.id}`, err);
          }
        }
      }),
    );
  },

  loadEarlierMessages: async (cid) => {
    const cursor = get().messagesNextCursor[cid];
    // Guards two things, keyed by `cid`: nothing more to load (mirrors the
    // control's own conditional render), and a load already in flight for
    // THIS conversation -- a double-click firing a second call before the
    // first's `await` below resolves would otherwise read this SAME cursor
    // and prepend this SAME page twice (the P10 lesson, review finding
    // I-1, `loadMoreConversations`'s own comment below). Set synchronously,
    // before the first `await`, so a second call arriving in the same tick
    // already sees it.
    if (!cursor || get().loadingEarlierMessages[cid]) return;
    set((s) => ({ loadingEarlierMessages: { ...s.loadingEarlierMessages, [cid]: true } }));
    try {
      const page = await api.getMessages(cid, cursor);
      set((s) => ({
        // PREPEND, not append: `page.items` is already oldest-to-newest
        // within itself (the wire contract get_messages still honors) and
        // is chronologically OLDER than every message already in
        // `messages[cid]` (the backend's next-page predicate now walks
        // strictly backward in time -- history.py's own amendment
        // docstring), so it goes whole at the front.
        messages: { ...s.messages, [cid]: [...page.items, ...(s.messages[cid] ?? [])] },
        messagesNextCursor: { ...s.messagesNextCursor, [cid]: page.next_cursor },
        // The true opener becomes knowable the instant next_cursor goes
        // null: this fetch just returned the OLDEST remaining page, so its
        // own first item is genuinely the conversation's first-ever
        // message (same reasoning as openConversation's own check above).
        openerIdByConv:
          page.next_cursor === null && page.items.length > 0
            ? { ...s.openerIdByConv, [cid]: page.items[0].id }
            : s.openerIdByConv,
      }));
      // Best-effort, same as openConversation's own call: covers both a
      // just-discovered opener (newly-eligible messages that were withheld
      // a moment ago) and, on every call, whatever turn-backed assistant
      // messages this page itself just added.
      await get().hydrateFeedback(cid);
    } finally {
      set((s) => ({ loadingEarlierMessages: { ...s.loadingEarlierMessages, [cid]: false } }));
    }
  },

  loadMoreConversations: async () => {
    const cursor = get().conversationsNextCursor;
    // Guards two things: nothing more to load (mirrors Sidebar's own
    // conditional render), and a load already in flight -- a double-click
    // firing a second call before the first's `await` below resolves would
    // otherwise read this SAME cursor and append this SAME page twice
    // (review finding I-1). Set synchronously, before the first `await`, so
    // a second call arriving in the same tick (no interleaving network
    // latency needed) already sees it.
    if (!cursor || get().loadingMoreConversations) return;
    set({ loadingMoreConversations: true });
    try {
      const page = await api.listConversations(cursor);
      set((s) => ({
        conversations: [...s.conversations, ...page.items],
        conversationsNextCursor: page.next_cursor,
      }));
    } finally {
      set({ loadingMoreConversations: false });
    }
  },

  sendMessage: async (cid, text, clientTurnKey) => {
    // One turn at a time per conversation. Without this, a second send started
    // while the first is streaming would clear `streamingByConv[cid]` in its own
    // `finally` and re-enable the composer mid-stream.
    if (get().streamingByConv[cid]) return;
    // Minted ONCE per logical send (poseidon-carryforwards.md's "Phase 6"
    // entry, closed): a caller with nothing to retry omits `clientTurnKey`
    // and gets a fresh one; a caller retrying the SAME logical send (the
    // backend's own `(user_sub, client_turn_key)` short-circuit in
    // orchestrator.py's `_begin_turn`) passes the key it already used back
    // in, verbatim -- never derived from `cid`/`text` alone, which would be
    // content-based replay detection, deliberately out of this task's scope
    // (doc-08: "true replay stays P11").
    const turnKey = clientTurnKey ?? crypto.randomUUID();
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      parts: [{ kind: "text", payload: { markdown: text } }],
    };
    set((s) => ({
      messages: { ...s.messages, [cid]: [...(s.messages[cid] ?? []), userMessage] },
      streamingByConv: { ...s.streamingByConv, [cid]: true },
    }));
    try {
      await streamTurn(cid, text, turnKey, (e) => get().applyEvent(cid, e));
    } catch (err) {
      // On-drop reconcile (doc 01 section 5, client rule 3): a stream that
      // errored mid-turn with a known turn_id (StreamError.turnId -- sse.ts's
      // own error path, set from the last envelope this stream actually
      // saw) means the run log may already hold a real answer this client
      // simply never received. GET /api/turns/{id} and materialize its
      // parts INSTEAD OF the generic error bubble; a turn_id we never saw
      // (a failure before the first frame), or a reconcile call that itself
      // fails, both fall through to that same generic bubble unchanged --
      // never worse than before this hook existed, only sometimes better.
      let reconciled = false;
      if (err instanceof StreamError && err.turnId) {
        try {
          const turn = await api.getTurn(err.turnId);
          if (turn.message) {
            const recovered: Message = {
              id: turn.message.id,
              role: "assistant",
              parts: turn.message.parts,
            };
            // Merge-by-id (final-review wave, I-3), not append: StreamError.
            // turnId is non-null only when at least one frame arrived
            // (sse.ts's own lastTurnId), and the first frame of every turn
            // is "accepted", which applyEventTo already pushed into the
            // store under this SAME id (sink.message_id, what turn.message.
            // id also is) -- so a message with this id is already present
            // on every real path that reaches here. Replace it in place
            // (same find-by-id discipline applyEventTo already uses);
            // append only when genuinely absent (defensive -- keeps this
            // hook safe even if that invariant ever changes).
            set((s) => {
              const current = s.messages[cid] ?? [];
              const index = current.findIndex((m) => m.id === recovered.id);
              const next =
                index >= 0
                  ? current.map((m, i) => (i === index ? recovered : m))
                  : [...current, recovered];
              return { messages: { ...s.messages, [cid]: next } };
            });
            reconciled = true;
          }
        } catch {
          // The reconcile call itself failed (still offline, 404, ...) --
          // fall through to the generic bubble below rather than leaving
          // the user with neither an answer nor an error shown.
        }
      }
      if (!reconciled) {
        const message = err instanceof Error ? err.message : String(err);
        const errorMessage: Message = {
          id: crypto.randomUUID(),
          role: "assistant",
          parts: [{ kind: "error", payload: { code: "stream_failed", message } }],
        };
        set((s) => ({
          messages: { ...s.messages, [cid]: [...(s.messages[cid] ?? []), errorMessage] },
        }));
      }
    } finally {
      set((s) => ({ streamingByConv: { ...s.streamingByConv, [cid]: false } }));
    }
  },

  applyEvent: (cid, e) => {
    // done's title is additive and non-null exactly once (the turn that
    // first names the conversation) -- computed here, outside `set`'s own
    // updater, so the narrowing from `e.name === "done"` survives into the
    // closure below without a cast.
    const title = e.name === "done" ? e.data.title : null;
    set((s) => ({
      messages: { ...s.messages, [cid]: applyEventTo(s.messages[cid] ?? [], e) },
      conversations:
        title == null
          ? s.conversations
          : s.conversations.map((c) => (c.id === cid ? { ...c, title } : c)),
    }));
  },

  submitFeedback: async (mid, verdict, comment) => {
    const prev = get().feedback[mid];
    set((s) => ({ feedback: { ...s.feedback, [mid]: { verdict, comment } } }));
    try {
      await api.postFeedback(mid, verdict, comment);
    } catch (err) {
      set((s) => {
        const next = { ...s.feedback };
        if (prev) next[mid] = prev;
        else delete next[mid];
        return { feedback: next };
      });
      throw err;
    }
  },
}));

/**
 * Test helper: return the store to a cold start. `setState` alone cannot reach
 * `bootstrapInFlight`, which is module state, so tests reset both through here.
 */
export function resetChatStore(): void {
  bootstrapInFlight = null;
  useChatStore.setState({
    conversations: [],
    conversationsNextCursor: null,
    loadingMoreConversations: false,
    activeId: null,
    messages: {},
    messagesNextCursor: {},
    loadingEarlierMessages: {},
    streamingByConv: {},
    feedback: {},
    openerIdByConv: {},
  });
}

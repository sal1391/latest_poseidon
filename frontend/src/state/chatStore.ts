import { create } from "zustand";
import type { Conversation, Message, MessagePart, SseEvent } from "../api/types";
import * as api from "./../api/client";
import { streamTurn } from "../api/sse";

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
  activeId: string | null;
  messages: Record<string, Message[]>;
  streamingByConv: Record<string, boolean>;
  feedback: Record<string, { verdict: "up" | "down"; comment?: string }>;
  bootstrap: () => Promise<void>;
  newConversation: () => Promise<string>;
  openConversation: (cid: string) => Promise<void>;
  sendMessage: (cid: string, text: string) => Promise<void>;
  applyEvent: (cid: string, e: SseEvent) => void;
  submitFeedback: (mid: string, verdict: "up" | "down", comment?: string) => Promise<void>;
}

/** Shared by every concurrent caller so one mount can only open one conversation. */
let bootstrapInFlight: Promise<void> | null = null;

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  activeId: null,
  messages: {},
  streamingByConv: {},
  feedback: {},

  bootstrap: () => {
    // Re-entrant: StrictMode's double effect, or a send issued before the first
    // list request lands, joins the in-flight run instead of starting a second.
    bootstrapInFlight ??= (async () => {
      const conversations = await api.listConversations();
      if (conversations.length === 0) {
        const { conversation, opener } = await api.createConversation();
        set((s) => ({
          conversations: [conversation],
          activeId: conversation.id,
          messages: { ...s.messages, [conversation.id]: [opener] },
        }));
        return;
      }
      set({ conversations });
      await get().openConversation(conversations[0].id);
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
    }));
    return conversation.id;
  },

  openConversation: async (cid) => {
    set({ activeId: cid });
    const messages = await api.getMessages(cid);
    set((s) => ({ messages: { ...s.messages, [cid]: messages } }));
  },

  sendMessage: async (cid, text) => {
    // One turn at a time per conversation. Without this, a second send started
    // while the first is streaming would clear `streamingByConv[cid]` in its own
    // `finally` and re-enable the composer mid-stream.
    if (get().streamingByConv[cid]) return;
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
      await streamTurn(cid, text, (e) => get().applyEvent(cid, e));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const errorMessage: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        parts: [{ kind: "error", payload: { code: "stream_failed", message } }],
      };
      set((s) => ({
        messages: { ...s.messages, [cid]: [...(s.messages[cid] ?? []), errorMessage] },
      }));
    } finally {
      set((s) => ({ streamingByConv: { ...s.streamingByConv, [cid]: false } }));
    }
  },

  applyEvent: (cid, e) => {
    set((s) => ({
      messages: { ...s.messages, [cid]: applyEventTo(s.messages[cid] ?? [], e) },
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
    activeId: null,
    messages: {},
    streamingByConv: {},
    feedback: {},
  });
}

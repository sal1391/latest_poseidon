import type { Conversation, Message } from "./types";

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(input, init);
  if (!r.ok) throw new Error(`request failed: ${r.status}`);
  return r;
}

export async function createConversation(): Promise<{ conversation: Conversation; opener: Message }> {
  const r = await apiFetch("/api/conversations", { method: "POST" });
  return (await r.json()) as { conversation: Conversation; opener: Message };
}

export async function listConversations(): Promise<Conversation[]> {
  const r = await apiFetch("/api/conversations");
  const body = (await r.json()) as { conversations: Conversation[] };
  return body.conversations;
}

export async function getMessages(cid: string): Promise<Message[]> {
  const r = await apiFetch(`/api/conversations/${cid}/messages`);
  const body = (await r.json()) as { messages: Message[] };
  return body.messages;
}

export async function postFeedback(
  mid: string,
  verdict: "up" | "down",
  comment?: string,
): Promise<void> {
  await apiFetch(`/api/messages/${mid}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ verdict, comment: comment ?? null }),
  });
}

import { notFound } from "next/navigation";
import { headers } from "next/headers";
import { Chat } from "./Chat";
import { loadConversation } from "../../../lib/conversations";
import { fromMessageParts, type PoseidonUIMessage } from "../../../lib/message-parts";
import { requireSales } from "../../../lib/request-identity";

export default async function Conversation({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { sub } = requireSales(await headers());
  const loaded = await loadConversation(sub, id);
  if (!loaded) notFound();
  /**
   * The history, in the UI-message shape `useChat` starts from. Loaded on the
   * server under the same RLS the rest of this app reads through, then handed
   * to the client component as its initial state -- so a reload shows exactly
   * the parts Python persisted, through the same renderers that drew them
   * live.
   */
  const initial: PoseidonUIMessage[] = loaded.messages.map((m) => ({
    id: m.id,
    role: m.role === "user" ? "user" : "assistant",
    parts: fromMessageParts(m.parts as { kind: string; payload: unknown }[]),
  }));
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-xl font-semibold">{loaded.conversation.title ?? "(untitled)"}</h1>
      <Chat conversationId={id} initialMessages={initial} />
    </main>
  );
}

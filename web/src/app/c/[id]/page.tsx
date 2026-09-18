import { notFound } from "next/navigation";
import { headers } from "next/headers";
import { loadConversation } from "../../../lib/conversations";

export default async function Conversation({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const h = await headers();
  const sub = h.get("x-poseidon-sub") ?? "dev|local";
  const loaded = await loadConversation(sub, id);
  if (!loaded) notFound();
  return (
    <main className="p-8">
      <h1 className="text-xl font-semibold">{loaded.conversation.title ?? "(untitled)"}</h1>
      <ol className="mt-4 space-y-4">
        {loaded.messages.map((m) => (
          <li key={m.id}>
            <span className="text-xs uppercase opacity-60">{m.role}</span>
            <pre className="whitespace-pre-wrap text-sm">{JSON.stringify(m.parts, null, 2)}</pre>
          </li>
        ))}
      </ol>
    </main>
  );
}

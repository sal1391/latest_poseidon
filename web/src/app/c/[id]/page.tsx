import { notFound } from "next/navigation";
import { headers } from "next/headers";
import { loadConversation } from "../../../lib/conversations";
import { requireSub } from "../../../lib/request-identity";

export default async function Conversation({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const sub = requireSub(await headers());
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

import Link from "next/link";
import { headers } from "next/headers";
import { listConversations } from "../lib/conversations";
import { requireSub } from "../lib/request-identity";

export default async function Home() {
  const sub = requireSub(await headers());
  const rows = await listConversations(sub);
  return (
    <main className="p-8">
      <h1 className="text-xl font-semibold">Conversations for {sub}</h1>
      <ul className="mt-4 space-y-2">
        {rows.map((c) => (
          <li key={c.id}>
            <Link className="underline" href={`/c/${c.id}`}>
              {c.title ?? "(untitled)"}
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}

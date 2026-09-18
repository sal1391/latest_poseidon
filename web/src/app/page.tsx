import Link from "next/link";
import { headers } from "next/headers";
import { listConversations } from "../lib/conversations";

export default async function Home() {
  const h = await headers();
  const sub = h.get("x-poseidon-sub") ?? "dev|local";
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

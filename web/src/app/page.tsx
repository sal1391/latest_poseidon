import Link from "next/link";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createConversation, listConversations } from "../lib/conversations";
import { requireSales } from "../lib/request-identity";

export default async function Home() {
  const { sub } = requireSales(await headers());
  const rows = await listConversations(sub);

  async function newConversation() {
    "use server";
    const { sub } = requireSales(await headers());
    const id = await createConversation(sub);
    redirect(`/c/${id}`);
  }

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
      <form action={newConversation} className="mt-4">
        <button type="submit" className="rounded border px-3 py-1">
          New conversation
        </button>
      </form>
    </main>
  );
}

import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";
import { listConversations } from "../lib/conversations";
import { requireSales } from "../lib/request-identity";
import { Shell } from "../ui/shell/Shell";
import { Sidebar } from "../ui/shell/Sidebar";

export const metadata: Metadata = {
  title: "Poseidon",
  description: "Chat-first sales intelligence for marine fuel sales",
};

/**
 * Every page renders inside the shell. Identity and the conversation list
 * are read here, once, under the same RLS the pages use; the proxy has
 * already refused anyone without Poseidon:Sales, so `requireSales` is the
 * belt to that brace. The list is a layout read, so it refreshes on the New
 * chat action's `revalidatePath("/", "layout")` and on a hard reload, not
 * after every turn (titles set by a turn appear on the next navigation).
 */
export default async function RootLayout({ children }: LayoutProps<"/">) {
  const { sub } = requireSales(await headers());
  const rows = await listConversations(sub);
  const conversations = rows.map((c) => ({ id: c.id, title: c.title }));
  return (
    <html lang="en" className="h-full antialiased">
      <body className="bg-surface font-sans text-ink">
        <Shell sidebar={<Sidebar conversations={conversations} sub={sub} />}>{children}</Shell>
      </body>
    </html>
  );
}

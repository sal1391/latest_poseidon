import { newConversation } from "../lib/actions";

/** The empty state inside the shell (spec 3.2). The sidebar is the list. */
export default function Home() {
  return (
    <section className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
      <h1 className="text-xl font-semibold">Ask Poseidon</h1>
      <p className="max-w-md text-[15px] text-ink-muted">Breakdowns, comparisons, briefs and monthly reports for your book.</p>
      <form action={newConversation}>
        <button type="submit" className="rounded-card border border-border bg-surface px-4 py-2 text-[13px] font-medium transition-shadow duration-[120ms] hover:shadow-1">
          New chat
        </button>
      </form>
    </section>
  );
}

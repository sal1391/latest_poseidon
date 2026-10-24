import { newConversation } from "../../lib/actions";
import { ConversationList, type ConversationRow } from "./ConversationList";

/**
 * The fixed left column (spec 3.2): brand, New chat, the conversation list
 * scrolling internally, and the signed-in sub at the foot so the dev
 * identity stays visible during walkthroughs. Synchronous: the layout does
 * the reading and passes rows in.
 */
export function Sidebar({ conversations, sub }: { conversations: ConversationRow[]; sub: string }) {
  return (
    <aside className="flex h-full min-h-0 flex-col gap-4 border-r border-border bg-surface-raised px-4 py-5" aria-label="Conversations">
      <div>
        <p className="text-xl font-semibold leading-tight">Poseidon</p>
        <p className="text-[12px] text-ink-muted">Sales intelligence</p>
      </div>
      <form action={newConversation}>
        <button
          type="submit"
          className="w-full rounded-card border border-border bg-surface px-3 py-2 text-[13px] font-medium transition-shadow duration-[120ms] hover:shadow-1"
        >
          New chat
        </button>
      </form>
      <ConversationList conversations={conversations} />
      <p className="mt-auto truncate text-[12px] text-ink-muted" title={sub}>
        Signed in as {sub}
      </p>
    </aside>
  );
}

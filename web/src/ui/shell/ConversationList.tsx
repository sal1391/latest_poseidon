"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

export interface ConversationRow {
  id: string;
  title: string | null;
}

/**
 * The sidebar's conversation rows. Client-side only so it can read the
 * active `[id]` segment with `useParams`; the rows themselves come from the
 * server layout. The active row is lifted onto the base surface with a 3px
 * accent bar drawn as an inset shadow so the row's width never changes.
 */
export function ConversationList({ conversations }: { conversations: ConversationRow[] }) {
  const params = useParams<{ id?: string }>();
  const activeId = typeof params?.id === "string" ? params.id : null;
  if (conversations.length === 0) {
    return <p className="px-2.5 text-[13px] text-ink-muted">No conversations yet</p>;
  }
  return (
    <nav className="min-h-0 flex-1 overflow-y-auto" aria-label="Conversation list">
      <ul className="space-y-0.5">
        {conversations.map((c) => {
          const active = c.id === activeId;
          const label = c.title ?? "(untitled)";
          const tone = c.title === null ? "text-ink-muted" : "text-ink";
          return (
            <li key={c.id}>
              <Link
                href={`/c/${c.id}`}
                title={label}
                aria-current={active ? "page" : undefined}
                className={`block truncate rounded-chip px-2.5 py-2 text-[13px] transition-colors duration-[120ms] ${tone} ${
                  active ? "bg-surface shadow-[inset_3px_0_0_0_var(--color-accent)]" : "hover:bg-surface"
                }`}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

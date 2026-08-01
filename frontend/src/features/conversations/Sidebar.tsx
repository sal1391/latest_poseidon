import { useChatStore } from "../../state/chatStore";
import { UserMenu } from "../auth/UserMenu";

/** Brand, new-chat action, the conversation list (active row highlighted),
 * and the user-menu slot (doc 01 section 3's ASCII layout) at the bottom. */
export function Sidebar() {
  const conversations = useChatStore((s) => s.conversations);
  const nextCursor = useChatStore((s) => s.conversationsNextCursor);
  const activeId = useChatStore((s) => s.activeId);
  const newConversation = useChatStore((s) => s.newConversation);
  const openConversation = useChatStore((s) => s.openConversation);
  const loadMoreConversations = useChatStore((s) => s.loadMoreConversations);

  return (
    <aside className="sidebar">
      <div className="brand">Poseidon</div>
      <button
        type="button"
        className="new-chat"
        onClick={() => {
          void newConversation().catch(() => undefined);
        }}
      >
        + New chat
      </button>
      <nav className="conversation-list" aria-label="Conversations">
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            type="button"
            className={
              conversation.id === activeId ? "conversation-item is-active" : "conversation-item"
            }
            aria-current={conversation.id === activeId ? "true" : undefined}
            onClick={() => {
              void openConversation(conversation.id).catch(() => undefined);
            }}
          >
            {conversation.title}
          </button>
        ))}
        {nextCursor !== null ? (
          <button
            type="button"
            // Reuses `.conversation-item`'s own row styling (no new CSS --
            // this task does no styling pass) with `load-more` as a stable,
            // unstyled hook for tests/future work.
            className="conversation-item load-more"
            onClick={() => {
              void loadMoreConversations().catch(() => undefined);
            }}
          >
            Load more
          </button>
        ) : null}
      </nav>
      <UserMenu />
    </aside>
  );
}

import { useChatStore } from "../../state/chatStore";

/** Brand, new-chat action and the conversation list (active row highlighted). */
export function Sidebar() {
  const conversations = useChatStore((s) => s.conversations);
  const activeId = useChatStore((s) => s.activeId);
  const newConversation = useChatStore((s) => s.newConversation);
  const openConversation = useChatStore((s) => s.openConversation);

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
      </nav>
    </aside>
  );
}

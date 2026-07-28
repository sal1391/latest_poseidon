import "../theme/tokens.css";
import "../theme/base.css";

export default function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">Poseidon</div>
        <button className="new-chat">+ New chat</button>
      </aside>
      <main className="chat-column">
        <div className="thread" />
        <div className="composer">
          <input placeholder="Message Poseidon…" aria-label="Message Poseidon" />
        </div>
      </main>
    </div>
  );
}

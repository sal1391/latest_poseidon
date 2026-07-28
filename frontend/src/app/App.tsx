import "../theme/tokens.css";
import "../theme/base.css";
import ChatScreen from "../features/chat/ChatScreen";

export default function App() {
  return (
    <div className="app-shell">
      <ChatScreen />
    </div>
  );
}

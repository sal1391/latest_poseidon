import "../theme/tokens.css";
import "../theme/base.css";
import { AuthGate } from "../features/auth/AuthGate";
import ChatScreen from "../features/chat/ChatScreen";

export default function App() {
  return (
    <AuthGate>
      <div className="app-shell">
        <ChatScreen />
      </div>
    </AuthGate>
  );
}

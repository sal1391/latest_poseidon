import type { ToolEventPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `tool_event` parts as a compact status line: dot while running, glyph once settled. */
export function ToolEventPart({ part }: PartProps) {
  const { status, label } = part.payload as ToolEventPayload;
  if (status === "start") {
    return (
      <div className="tool-step">
        <span className="tool-dot" aria-hidden="true" />
        {label}
      </div>
    );
  }
  const glyph = status === "done" ? "✓" : "✕";
  const style = status === "error" ? { color: "var(--negative)" } : undefined;
  return (
    <div className="tool-step" style={style}>
      {glyph} {label}
    </div>
  );
}

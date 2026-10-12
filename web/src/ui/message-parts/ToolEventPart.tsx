import type { PartProps, ToolEventPayload } from "./registry";

const GLYPH: Record<"done" | "error", string> = {
  done: "✓", // checkmark
  error: "✕", // cross
};

/** Renders `tool_event` parts as a compact status line: a pulsing dot while
 * running (Tailwind `animate-pulse`, replacing the Vite original's CSS
 * `tool-dot-pulse` keyframe -- see `frontend/src/theme/base.css` -- as the
 * user's "still working" cue during a tool call), settling to a glyph once
 * done (`✓`) or errored (`✕`), followed by the label. */
export function ToolEventPart({ part }: PartProps) {
  const { status, label } = part.payload as ToolEventPayload;
  const isError = status === "error";
  return (
    <div className={`flex items-center gap-2 text-sm ${isError ? "text-red-600 dark:text-red-400" : "text-neutral-600 dark:text-neutral-400"}`}>
      {status === "start" ? (
        <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full bg-current animate-pulse" />
      ) : (
        <span aria-hidden="true">{GLYPH[status]}</span>
      )}
      <span>{label}</span>
    </div>
  );
}

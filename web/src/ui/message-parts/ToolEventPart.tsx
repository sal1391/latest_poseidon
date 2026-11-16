import type { PartProps, ToolEventPayload } from "./registry";

const GLYPH: Record<"done" | "error", string> = {
  done: "✓", // checkmark
  error: "✕", // cross
};

/** Renders `tool_event` parts as a compact status line: a pulsing dot while
 * running (Tailwind `animate-pulse`, replacing the Vite original's CSS
 * `tool-dot-pulse` keyframe -- see `frontend/src/theme/base.css` -- as the
 * user's "still working" cue during a tool call), settling to a glyph once
 * done (`✓`) or errored (`✕`), followed by the label. Muted ink, negative
 * ink on error (spec 4). */
export function ToolEventPart({ part }: PartProps) {
  const { status, label } = part.payload as ToolEventPayload;
  const isError = status === "error";
  return (
    <div className={`flex items-center gap-2 text-[13px] ${isError ? "text-negative" : "text-ink-muted"}`}>
      {status === "start" ? (
        <span aria-hidden="true" className="inline-block h-2 w-2 animate-pulse rounded-full bg-current" />
      ) : (
        <span aria-hidden="true">{GLYPH[status]}</span>
      )}
      <span>{label}</span>
    </div>
  );
}

import type { PartProps, ToolEventPayload } from "./registry";

const GLYPH: Record<ToolEventPayload["status"], string> = {
  start: "…",
  done: "✓", // checkmark
  error: "✕", // cross
};

/** Renders `tool_event` parts as a compact status line: a glyph per status
 * (`…` while running, `✓` once done, `✕` on error) followed by the label. */
export function ToolEventPart({ part }: PartProps) {
  const { status, label } = part.payload as ToolEventPayload;
  const isError = status === "error";
  return (
    <div className={`flex items-center gap-2 text-sm ${isError ? "text-red-600 dark:text-red-400" : "text-neutral-600 dark:text-neutral-400"}`}>
      <span aria-hidden="true">{GLYPH[status]}</span>
      <span>{label}</span>
    </div>
  );
}

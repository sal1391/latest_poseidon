import type { ErrorPayload, PartProps } from "./registry";

/** Renders `error` parts as a card with a negative left rule: the message
 * in ink, the optional hint beneath in muted ink. No tinted fill (spec 4). */
export function ErrorPart({ part }: PartProps) {
  const { message, hint } = part.payload as ErrorPayload;
  return (
    <div className="rounded-card border border-border border-l-4 border-l-negative bg-surface p-3 text-[13px]">
      <p className="text-ink">{message}</p>
      {hint ? <p className="mt-1 text-ink-muted">{hint}</p> : null}
    </div>
  );
}

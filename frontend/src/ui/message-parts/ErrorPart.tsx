import type { ErrorPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `error` parts as a bordered card with the message and an optional hint. */
export function ErrorPart({ part }: PartProps) {
  const { message, hint } = part.payload as ErrorPayload;
  return (
    <div className="error-card">
      <p>{message}</p>
      {hint ? <p>{hint}</p> : null}
    </div>
  );
}

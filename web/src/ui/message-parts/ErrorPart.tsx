import type { ErrorPayload, PartProps } from "./registry";

/** Renders `error` parts as a bordered card with the message and an optional hint. */
export function ErrorPart({ part }: PartProps) {
  const { message, hint } = part.payload as ErrorPayload;
  return (
    <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
      <p>{message}</p>
      {hint ? <p className="mt-1 text-red-600 dark:text-red-400">{hint}</p> : null}
    </div>
  );
}

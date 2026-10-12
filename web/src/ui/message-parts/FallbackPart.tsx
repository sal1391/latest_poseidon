import type { PartProps } from "./registry";

/** Safe fallback for any part kind the registry doesn't recognize yet. */
export function FallbackPart({ part }: PartProps) {
  const { kind, payload } = part;
  return (
    <details className="rounded-md border border-neutral-200 p-2 text-sm dark:border-neutral-800">
      <summary className="cursor-pointer font-medium">{kind}</summary>
      <pre className="mt-1 overflow-x-auto text-xs">{JSON.stringify(payload, null, 2)}</pre>
    </details>
  );
}

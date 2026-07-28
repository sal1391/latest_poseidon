import type { PartProps } from "./registry";

/** Safe fallback for any part kind the registry doesn't recognize yet. */
export function FallbackPart({ part }: PartProps) {
  const { kind, payload } = part;
  return (
    <details className="fallback-part">
      <summary>Unsupported part: {kind}</summary>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </details>
  );
}

import type { PartProps, ProofPayload } from "./registry";

/** Renders `proof` parts as a collapsible provenance block, one line per
 * `payload.lines` entry, closed by default. */
export function ProofPart({ part }: PartProps) {
  const { lines } = part.payload as ProofPayload;
  return (
    <details className="rounded-md border border-neutral-200 p-2 text-sm dark:border-neutral-800">
      <summary className="cursor-pointer font-medium">Proof</summary>
      <ul className="mt-1 list-inside list-disc space-y-0.5">
        {lines.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
    </details>
  );
}

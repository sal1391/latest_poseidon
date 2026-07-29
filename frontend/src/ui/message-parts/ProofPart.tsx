import type { ProofPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `proof` parts as a collapsible "How this was computed" block,
 * one line per `payload.lines` entry, closed by default (the same
 * collapsible-details idiom `FallbackPart` already uses). */
export function ProofPart({ part }: PartProps) {
  const { lines } = part.payload as ProofPayload;
  return (
    <details className="proof-part">
      <summary>How this was computed</summary>
      <ul>
        {lines.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
    </details>
  );
}

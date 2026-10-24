import { Disclosure } from "../primitives/Disclosure";
import type { PartProps, ProofPayload } from "./registry";

/** Renders `proof` parts as a collapsible provenance block, one line per
 * `payload.lines` entry, closed by default (unchanged). The summary reads
 * "How this was computed" (spec 4). */
export function ProofPart({ part }: PartProps) {
  const { lines } = part.payload as ProofPayload;
  return (
    <Disclosure summary="How this was computed">
      <ul className="list-inside list-disc space-y-1 text-[13px] text-ink-muted">
        {lines.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
    </Disclosure>
  );
}

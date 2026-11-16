import { Disclosure } from "../primitives/Disclosure";
import type { ReportPayload } from "./types";

/** The report's provenance: the proof lines, then one line per query. The
 * shared disclosure, closed by default (spec 5). */
export function ProofBlock({ proof, queries }: Pick<ReportPayload, "proof" | "queries">) {
  const lines = [...proof, ...queries.map((q) => `${q.label} ${q.sql_hash} rows=${q.rows} ms=${q.ms}`)];
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

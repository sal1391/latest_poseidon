import type { ReportPayload } from "./types";

export function ProofBlock({ proof, queries }: Pick<ReportPayload, "proof" | "queries">) {
  const lines = [...proof, ...queries.map((q) => `${q.label} ${q.sql_hash} rows=${q.rows} ms=${q.ms}`)];
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

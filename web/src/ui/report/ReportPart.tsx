import type { PartProps } from "../message-parts/registry";
import { KpiTiles } from "./KpiTiles";
import { MoverCards } from "./MoverCards";
import { Narrative } from "./Narrative";
import { NewLostTables } from "./NewLostTables";
import { ProofBlock } from "./ProofBlock";
import { RankedTables } from "./RankedTables";
import type { ReportPayload } from "./types";

/** Renders `report` parts. Suggested questions arrive as a separate chips part (ruling 9). */
export function ReportPart({ part }: PartProps) {
  const p = part.payload as ReportPayload;
  return (
    <article className="space-y-4 rounded-md border border-neutral-200 p-3 dark:border-neutral-800">
      <header className="space-y-0.5">
        <h3 className="text-base font-semibold">{p.scope.label}</h3>
        <p className="text-sm">
          {`${p.periods.current.label} vs ${p.periods.prior.label}`}
          {p.periods.window_kind === "mtd" ? (
            <span className="ml-2 rounded bg-amber-100 px-1.5 text-xs text-amber-900 dark:bg-amber-900 dark:text-amber-100">Month to date</span>
          ) : null}
        </p>
        <p className="text-xs text-neutral-500">{`Generated ${p.generated_at} | Source: ${p.source}`}</p>
      </header>
      <KpiTiles kpis={p.kpis} />
      <RankedTables tables={p.tables} entityLabel={p.entity.label} />
      <MoverCards movers={p.movers} context={p.entity_context} />
      <NewLostTables newLost={p.new_lost} />
      <Narrative narrative={p.narrative} />
      <ProofBlock proof={p.proof} queries={p.queries} />
    </article>
  );
}

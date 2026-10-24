import { Badge } from "../primitives/Badge";
import type { PartProps } from "../message-parts/registry";
import { KpiTiles } from "./KpiTiles";
import { MoverCards } from "./MoverCards";
import { Narrative } from "./Narrative";
import { NewLostTables } from "./NewLostTables";
import { ProofBlock } from "./ProofBlock";
import { RankedTables } from "./RankedTables";
import type { ReportPayload } from "./types";

/** Renders `report` parts as a document card inside the thread (spec 5).
 * Suggested questions arrive as a separate chips part (ruling 9) and render
 * outside the card. */
export function ReportPart({ part }: PartProps) {
  const p = part.payload as ReportPayload;
  return (
    <article className="space-y-5 rounded-card border border-border bg-surface p-5 shadow-1">
      <header className="space-y-1">
        <h3 className="text-xl font-semibold">{p.scope.label}</h3>
        <p className="text-[15px] text-ink">
          {`${p.periods.current.label} vs ${p.periods.prior.label}`}
          {p.periods.window_kind === "mtd" ? (
            <span className="ml-2">
              <Badge tone="warn">Month to date</Badge>
            </span>
          ) : null}
        </p>
        <p className="text-[12px] text-ink-muted">{`Generated ${p.generated_at} · Source: ${p.source}`}</p>
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

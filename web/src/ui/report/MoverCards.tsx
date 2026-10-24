import { Badge } from "../primitives/Badge";
import { toneFor } from "./tone";
import type { EntityContext, Mover } from "./types";

function Card({ mover, ctx }: { mover: Mover; ctx?: EntityContext }) {
  const gp = mover.display.GP;
  return (
    <li className="rounded-card border border-border p-3 text-[13px]">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{mover.key}</span>
        <Badge>{mover.driver}</Badge>
      </div>
      <div className="tabular-nums">{`GP ${gp.prior} → ${gp.current}`}</div>
      <div className={`tabular-nums ${toneFor(mover.change.GP)}`}>{`${gp.change} (${gp.pct})`}</div>
      {ctx ? (
        <div className="mt-1 text-[12px] text-ink-muted tabular-nums">
          <div>{`Office ${ctx.reconciliation.display.office_change} · other offices ${ctx.reconciliation.display.other_offices_net} · book ${ctx.reconciliation.display.book_change}`}</div>
          <div>{`share of book ${ctx.share_of_book.display.prior} → ${ctx.share_of_book.display.current}`}</div>
        </div>
      ) : null}
    </li>
  );
}

/** Two columns, Increases and Decreases, one card per mover (spec 5): the
 * driver as a badge, the GP line with the arrow glyph, the change coloured
 * by sign (ruling P3-R2), the office reconciliation and share-of-book lines
 * muted and only when the report has entity context. */
export function MoverCards({ movers, context }: { movers: { increases: Mover[]; decreases: Mover[] }; context: Record<string, EntityContext> | null }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {(
        [
          ["Increases", movers.increases],
          ["Decreases", movers.decreases],
        ] as const
      ).map(([title, list]) => (
        <div key={title}>
          <h4 className="mb-2 text-base font-semibold">{title}</h4>
          <ul className="space-y-2">
            {list.map((m) => (
              <Card key={m.key} mover={m} ctx={context?.[m.key]} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

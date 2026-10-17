import type { EntityContext, Mover } from "./types";

function Card({ mover, ctx }: { mover: Mover; ctx?: EntityContext }) {
  const gp = mover.display.GP;
  return (
    <li className="rounded-md border border-neutral-200 p-2 text-sm dark:border-neutral-800">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{mover.key}</span>
        <span className="rounded bg-neutral-100 px-1.5 text-xs dark:bg-neutral-800">{mover.driver}</span>
      </div>
      <div className="tabular-nums">{`GP ${gp.prior} -> ${gp.current}`}</div>
      <div className="tabular-nums">{`${gp.change} (${gp.pct})`}</div>
      {ctx ? (
        <div className="mt-1 text-xs text-neutral-500 tabular-nums">
          <div>{`Office ${ctx.reconciliation.display.office_change} | other offices ${ctx.reconciliation.display.other_offices_net} | book ${ctx.reconciliation.display.book_change}`}</div>
          <div>{`share of book ${ctx.share_of_book.display.prior} -> ${ctx.share_of_book.display.current}`}</div>
        </div>
      ) : null}
    </li>
  );
}

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
          <h4 className="mb-1 text-sm font-medium">{title}</h4>
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

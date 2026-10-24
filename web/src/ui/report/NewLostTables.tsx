import { TABLE, TD, TD_NUM, TH, TH_NUM, TR } from "../primitives/data-table";
import type { Row } from "./types";

function Block({ title, rows, which }: { title: string; rows: Row[]; which: "current" | "prior" }) {
  return (
    <div>
      <h4 className="mb-2 text-base font-semibold">{title}</h4>
      {rows.length === 0 ? (
        <p className="text-[13px] text-ink-muted">none</p>
      ) : (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <thead>
              <tr>
                <th className={TH}>Name</th>
                <th className={TH_NUM}>GP</th>
                <th className={TH_NUM}>Volume</th>
                <th className={TH_NUM}>Margin</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className={TR}>
                  <td className={TD}>{r.key}</td>
                  <td className={TD_NUM}>{r.display.GP[which]}</td>
                  <td className={TD_NUM}>{r.display.VOLUME[which]}</td>
                  <td className={TD_NUM}>{r.display.MARGIN[which]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** New (current-period values) and Lost (prior-period values) side by side
 * from 640px (spec 5). */
export function NewLostTables({ newLost }: { newLost: { new: Row[]; lost: Row[] } }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Block title="New" rows={newLost.new} which="current" />
      <Block title="Lost" rows={newLost.lost} which="prior" />
    </div>
  );
}

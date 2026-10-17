import type { Row } from "./types";

const TH = "border-b border-neutral-300 px-2 py-1 text-left font-medium dark:border-neutral-700";
const TD = "border-b border-neutral-200 px-2 py-1 text-right tabular-nums dark:border-neutral-800";

function Block({ title, rows, which }: { title: string; rows: Row[]; which: "current" | "prior" }) {
  return (
    <div>
      <h4 className="mb-1 text-sm font-medium">{title}</h4>
      {rows.length === 0 ? (
        <p className="text-sm text-neutral-500">none</p>
      ) : (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className={TH}>Name</th>
              <th className={TH}>GP</th>
              <th className={TH}>Volume</th>
              <th className={TH}>Margin</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td className="border-b border-neutral-200 px-2 py-1 dark:border-neutral-800">{r.key}</td>
                <td className={TD}>{r.display.GP[which]}</td>
                <td className={TD}>{r.display.VOLUME[which]}</td>
                <td className={TD}>{r.display.MARGIN[which]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function NewLostTables({ newLost }: { newLost: { new: Row[]; lost: Row[] } }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Block title="New" rows={newLost.new} which="current" />
      <Block title="Lost" rows={newLost.lost} which="prior" />
    </div>
  );
}

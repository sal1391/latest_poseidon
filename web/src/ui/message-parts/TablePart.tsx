import { TABLE, TD, TD_NUM, TH, TR } from "../primitives/data-table";
import type { PartProps, TablePayload } from "./registry";

/** Renders `table` parts as a plain table inside a horizontal scroll
 * wrapper: one header cell per column, numeric cells right-aligned with
 * tabular figures so a column of amounts lines up on its decimal point,
 * rows lifting to the raised surface on hover (spec 4). */
export function TablePart({ part }: PartProps) {
  const { columns, rows } = part.payload as TablePayload;
  return (
    <div className="overflow-x-auto">
      <table className={TABLE}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className={TH}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className={TR}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className={typeof cell === "number" ? TD_NUM : TD}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

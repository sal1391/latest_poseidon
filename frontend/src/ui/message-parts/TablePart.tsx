import type { TablePayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `table` parts as a theme-tokened table: one header cell per
 * column, numeric cells set in the tabular-numeral `.data` face (see
 * theme/base.css) so a column of amounts lines up on its decimal point. */
export function TablePart({ part }: PartProps) {
  const { columns, rows } = part.payload as TablePayload;
  return (
    <table className="table-part">
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column}>{column}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, rowIndex) => (
          <tr key={rowIndex}>
            {row.map((cell, cellIndex) => (
              <td key={cellIndex} className={typeof cell === "number" ? "data" : undefined}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

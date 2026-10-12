import type { PartProps, TablePayload } from "./registry";

/** Renders `table` parts as a plain table: one header cell per column,
 * numeric cells right-aligned with tabular figures so a column of amounts
 * lines up on its decimal point. */
export function TablePart({ part }: PartProps) {
  const { columns, rows } = part.payload as TablePayload;
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column} className="border-b border-neutral-300 px-2 py-1 text-left font-medium dark:border-neutral-700">
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, rowIndex) => (
          <tr key={rowIndex}>
            {row.map((cell, cellIndex) => (
              <td
                key={cellIndex}
                className={`border-b border-neutral-200 px-2 py-1 dark:border-neutral-800 ${typeof cell === "number" ? "text-right tabular-nums" : ""}`}
              >
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Cell classes shared by TablePart (spec 4) and the report tables (spec 5).
 * Constants rather than a component: the report tables have their own
 * header structure (a spanning row above the column headers) and footer,
 * so what they share with the chat table is the cells, not the markup.
 */
export const TABLE = "w-full border-collapse text-[13px]";
const TH_BASE = "border-b border-border px-2 pb-1 text-[12px] font-medium uppercase tracking-wide text-ink-muted";
export const TH = `${TH_BASE} text-left`;
export const TH_NUM = `${TH_BASE} text-right`;
export const TR = "hover:bg-surface-raised";
export const TD = "border-b border-border px-2 py-1.5";
export const TD_NUM = `${TD} text-right tabular-nums`;

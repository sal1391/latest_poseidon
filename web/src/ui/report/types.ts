export interface DeltaDisplay {
  prior: string;
  current: string;
  change: string;
  pct: string;
}
export interface Delta {
  prior: number;
  current: number;
  change: number;
  pct_change: number | null;
  display: DeltaDisplay;
}
export type MetricKey = "GP" | "VOLUME" | "MARGIN";
export interface Row {
  key: string;
  status: string;
  prior: Record<MetricKey, number>;
  current: Record<MetricKey, number>;
  change: Record<MetricKey, number>;
  pct: Record<MetricKey, number | null>;
  display: Record<MetricKey, DeltaDisplay>;
}
export interface RankedTable {
  rows: Row[];
  other: Row | null;
  footer: Row;
}
export interface Mover extends Row {
  driver: string;
}
export interface EntityContext {
  reconciliation: {
    display: { office_change: string; other_offices_net: string; book_change: string };
  };
  share_of_book: { display: { prior: string; current: string } };
}
export interface ReportNarrative {
  template_md: string;
  llm_md: string | null;
  grounded: boolean;
  flags: string[];
}
export interface ReportPayload {
  entity: { column: string; label: string };
  entity_context: Record<string, EntityContext> | null;
  generated_at: string;
  source: string;
  kpis: Record<string, Delta>;
  movers: { increases: Mover[]; decreases: Mover[] };
  narrative: ReportNarrative;
  new_lost: { new: Row[]; lost: Row[] };
  periods: {
    current: { label: string };
    prior: { label: string };
    window_kind: string;
  };
  proof: string[];
  queries: { label: string; sql_hash: string; rows: number; ms: number }[];
  scope: { label: string };
  tables: {
    entities_by_gp: RankedTable;
    entities_by_volume: RankedTable;
    ports_by_gp: RankedTable;
    ports_by_volume: RankedTable;
    deal_class: RankedTable;
  };
}

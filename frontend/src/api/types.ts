export type PartKind =
  | "text" | "chips" | "tool_event" | "error" | "table" | "proof" | "metric_grid" | "artifact"
  | "phase_section";
export interface TextPayload { markdown: string }
// send_text: orchestrator.py's own _finish_clarify sends a "for <name>"
// cue on clarification chips, and (P8 whole-branch final-review wave,
// 2026-07-30, item 9 / M-6 -- corrects this comment's own earlier claim)
// api/live_chat.py's opener chips carry it too, the pinned D19 entry
// phrase ("start an existing-customer brief" / "start a new-prospect
// brief") -- NOT scoped to clarification chips alone as this comment used
// to say. ChipsPart's own `?? label` fallback exists for whatever OTHER
// chip payload carries no send_text at all, not for the opener
// specifically.
export interface ChipOption { id: string; label: string; send_text?: string }
export interface ChipsPayload { options: ChipOption[]; disabled?: boolean }
export interface ToolEventPayload {
  tool_seq: number; tool: string;
  // string for an MCP-style external tool server; null for an in-process
  // skill dispatch (the live sink's own SseEnvelopeSink._send_tool_frame
  // sends null for every dispatch today — see that module's docstring).
  // Zero consumers of this field read `server` today (verified), so this
  // widening is additive and non-breaking.
  server: string | null;
  status: "start" | "done" | "error"; label: string;
}
export interface ErrorPayload { code: string; message: string; hint?: string }
export interface TablePayload { columns: string[]; rows: (string | number)[][] }
export interface ProofPayload { lines: string[] }
// phase_section's real producer: poseidon.core.skills.result.
// phase_section_part (backend/poseidon/core/skills/result.py) -- one
// titled block of markdown per completed agent phase of a brief
// (contextualize / each research lens call / strategize). The payload
// ships `title`, never `phase` (doc 01 section 4's own table names the
// field `phase`; item 9 of this wave corrects that row to match this
// shipped shape -- see that doc's own git history for the correction).
export interface PhaseSectionPayload { title: string; markdown: string }
// metric_grid's real producer: poseidon.tasks.data_qa.skills.metric_query.
// tools.format_parts._metric_grid_parts (backend/.../tools/format_parts.py)
// -- "a"/"b" are the two compared periods' ISO date bounds, never rendered
// text; a metric's own "a"/"b" is `null` exactly when that side's query
// returned no rows (format_parts.py's own "one side empty is still an
// answer" rule -- never a fabricated 0), and "unit" is `null` for a metric
// with no certified unit (e.g. MARGIN/WIN_RATE -- see format_parts.py's own
// _DISPLAY_OVERRIDES).
export interface MetricGridPeriod { start: string; end: string }
export interface MetricGridMetric {
  name: string; friendly: string; a: number | null; b: number | null; unit: string | null;
}
export interface MetricGridPayload {
  periods: { a: MetricGridPeriod; b: MetricGridPeriod };
  metrics: MetricGridMetric[];
}
// artifact's payload: poseidon.core.skills.context.ArtifactRef flattened to
// its three plain fields (see that dataclass's own docstring) -- `url` is a
// presigned GET the browser fetches directly, never a backend proxy route.
export interface ArtifactPayload { name: string; url: string; mime: string }
export interface MessagePart { kind: string; payload: unknown }
export interface Message {
  id: string;
  role: "user" | "assistant";
  parts: MessagePart[];
  lastSeq?: number; // highest applied event_seq (client-side replay guard)
}
export interface Conversation { id: string; title: string }
// GET /api/skills's wire shape (poseidon.api.live_chat.list_skills) -- no
// curated example prompt field; see SkillsPicker.tsx's own fallback list for
// where an example comes from instead.
export interface SkillSummary { id: string; label: string; description: string }
export interface SseEnvelope { turn_id: string; message_id: string; event_seq: number }
export type SseEvent =
  | { name: "accepted"; data: SseEnvelope & { turn_index: number } }
  | { name: "tool"; data: SseEnvelope & ToolEventPayload }
  | { name: "token"; data: SseEnvelope & { text: string } }
  | { name: "part"; data: SseEnvelope & MessagePart }
  | { name: "phase"; data: SseEnvelope & { phase: string; status: "start" | "done" } }
  | { name: "done"; data: SseEnvelope & { usage: unknown } }
  | { name: "error"; data: SseEnvelope & ErrorPayload };

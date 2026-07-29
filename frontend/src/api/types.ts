export type PartKind = "text" | "chips" | "tool_event" | "error" | "table" | "proof";
export interface TextPayload { markdown: string }
export interface ChipOption { id: string; label: string }
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

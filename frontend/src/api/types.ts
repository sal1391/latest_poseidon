export type PartKind = "text" | "chips" | "tool_event" | "error";
export interface TextPayload { markdown: string }
export interface ChipOption { id: string; label: string }
export interface ChipsPayload { options: ChipOption[]; disabled?: boolean }
export interface ToolEventPayload {
  tool_seq: number; tool: string; server: string;
  status: "start" | "done" | "error"; label: string;
}
export interface ErrorPayload { code: string; message: string; hint?: string }
export interface MessagePart { kind: string; payload: unknown }
export interface Message {
  id: string;
  role: "user" | "assistant";
  parts: MessagePart[];
  lastSeq?: number; // highest applied event_seq (client-side replay guard)
}
export interface Conversation { id: string; title: string }
export interface SseEnvelope { turn_id: string; message_id: string; event_seq: number }
export type SseEvent =
  | { name: "accepted"; data: SseEnvelope & { turn_index: number } }
  | { name: "tool"; data: SseEnvelope & ToolEventPayload }
  | { name: "token"; data: SseEnvelope & { text: string } }
  | { name: "part"; data: SseEnvelope & MessagePart }
  | { name: "phase"; data: SseEnvelope & { phase: string; status: "start" | "done" } }
  | { name: "done"; data: SseEnvelope & { usage: unknown } }
  | { name: "error"; data: SseEnvelope & ErrorPayload };

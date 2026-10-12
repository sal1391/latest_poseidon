import type { ComponentType } from "react";
import type { MessagePart } from "../../lib/message-parts";
import { ArtifactPart } from "./ArtifactPart";
import { ChipsPart } from "./ChipsPart";
import { ErrorPart } from "./ErrorPart";
import { FallbackPart } from "./FallbackPart";
import { MetricGridPart } from "./MetricGridPart";
import { PhaseSectionPart } from "./PhaseSectionPart";
import { ProofPart } from "./ProofPart";
import { TablePart } from "./TablePart";
import { TextPart } from "./TextPart";
import { ToolEventPart } from "./ToolEventPart";

// Payload interfaces copied from frontend/src/api/types.ts (the Vite app's
// wire types) -- ported, not imported: the two apps are on different React
// majors, but the wire shapes are identical since both talk to the same
// backend.
export interface TextPayload {
  markdown: string;
}
export interface ChipOption {
  id: string;
  label: string;
  send_text?: string;
}
export interface ChipsPayload {
  options: ChipOption[];
  disabled?: boolean;
}
export interface ToolEventPayload {
  tool_seq: number;
  tool: string;
  server: string | null;
  status: "start" | "done" | "error";
  label: string;
}
export interface ErrorPayload {
  code: string;
  message: string;
  hint?: string;
}
export interface TablePayload {
  columns: string[];
  rows: (string | number)[][];
}
export interface ProofPayload {
  lines: string[];
}
export interface PhaseSectionPayload {
  title: string;
  markdown: string;
}
export interface MetricGridPeriod {
  start: string;
  end: string;
}
export interface MetricGridMetric {
  name: string;
  friendly: string;
  a: number | null;
  b: number | null;
  unit: string | null;
}
export interface MetricGridPayload {
  periods: { a: MetricGridPeriod; b: MetricGridPeriod };
  metrics: MetricGridMetric[];
}
export interface ArtifactPayload {
  name: string;
  url: string;
  mime: string;
}

export interface PartProps {
  part: MessagePart;
  onChipSelect?: (id: string, sendText: string) => void;
  disabled?: boolean;
}

// The nine registered kinds plus FallbackPart = doc 01 section 4's ten.
const registry: Record<string, ComponentType<PartProps>> = {
  text: TextPart,
  chips: ChipsPart,
  tool_event: ToolEventPart,
  error: ErrorPart,
  table: TablePart,
  proof: ProofPart,
  metric_grid: MetricGridPart,
  artifact: ArtifactPart,
  phase_section: PhaseSectionPart,
};

export function PartRenderer(props: PartProps) {
  const Renderer = registry[props.part.kind] ?? FallbackPart;
  return <Renderer {...props} />;
}

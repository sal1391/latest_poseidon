import type { ComponentType } from "react";
import type { MessagePart } from "../../api/types";
import { TextPart } from "./TextPart";
import { ChipsPart } from "./ChipsPart";
import { ToolEventPart } from "./ToolEventPart";
import { ErrorPart } from "./ErrorPart";
import { TablePart } from "./TablePart";
import { ProofPart } from "./ProofPart";
import { MetricGridPart } from "./MetricGridPart";
import { ArtifactPart } from "./ArtifactPart";
import { PhaseSectionPart } from "./PhaseSectionPart";
import { FallbackPart } from "./FallbackPart";

export interface PartProps {
  part: MessagePart;
  onChipSelect?: (id: string, label: string) => void;
  /** True while the active conversation has a send in flight (ChatScreen's
   * own `sendingFor` gate) -- read by `ChipsPart` so a chip cannot dispatch
   * a second turn on top of one already streaming. Other part renderers
   * ignore it. */
  disabled?: boolean;
}

const registry: Record<string, ComponentType<PartProps>> = {
  text: TextPart,
  chips: ChipsPart,
  tool_event: ToolEventPart,
  error: ErrorPart,
  table: TablePart,
  proof: ProofPart,
  // metric_grid/artifact: Phase 8 Task 1 -- the brief flows' own first
  // producers (metric_grid already real since P3's format_parts.py;
  // artifact reachable for the first time now that loop.py forwards
  // SkillResult.artifacts -- see core/llm/loop.py and core/chat/events.py).
  metric_grid: MetricGridPart,
  artifact: ArtifactPart,
  // phase_section: P8 whole-branch final-review wave (2026-07-30), item 1
  // -- the brief flows' own narrative parts (contextualize / each
  // research lens call / strategize) had no renderer until now; both
  // briefs' phase content fell back to FallbackPart's raw-JSON dump (data
  // correct, presentation missing -- Task 5's own T5 E2E capture).
  phase_section: PhaseSectionPart,
};

export function PartRenderer(props: PartProps) {
  const Renderer = registry[props.part.kind] ?? FallbackPart;
  return <Renderer {...props} />;
}

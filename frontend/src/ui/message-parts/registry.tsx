import type { ComponentType } from "react";
import type { MessagePart } from "../../api/types";
import { TextPart } from "./TextPart";
import { ChipsPart } from "./ChipsPart";
import { ToolEventPart } from "./ToolEventPart";
import { ErrorPart } from "./ErrorPart";
import { TablePart } from "./TablePart";
import { ProofPart } from "./ProofPart";
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
  // metric_grid/artifact stay on FallbackPart until Phase 8 (first producer)
  // -- table/proof below are the only two kinds the flagship live turn
  // (Phase 6) actually emits beyond what was already registered.
  table: TablePart,
  proof: ProofPart,
};

export function PartRenderer(props: PartProps) {
  const Renderer = registry[props.part.kind] ?? FallbackPart;
  return <Renderer {...props} />;
}

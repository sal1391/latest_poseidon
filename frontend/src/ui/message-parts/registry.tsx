import type { ComponentType } from "react";
import type { MessagePart } from "../../api/types";
import { TextPart } from "./TextPart";
import { ChipsPart } from "./ChipsPart";
import { ToolEventPart } from "./ToolEventPart";
import { ErrorPart } from "./ErrorPart";
import { FallbackPart } from "./FallbackPart";

export interface PartProps {
  part: MessagePart;
  onChipSelect?: (id: string, label: string) => void;
}

const registry: Record<string, ComponentType<PartProps>> = {
  text: TextPart,
  chips: ChipsPart,
  tool_event: ToolEventPart,
  error: ErrorPart,
};

export function PartRenderer(props: PartProps) {
  const Renderer = registry[props.part.kind] ?? FallbackPart;
  return <Renderer {...props} />;
}

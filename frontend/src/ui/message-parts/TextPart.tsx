import Markdown from "react-markdown";
import type { TextPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `text` parts as markdown using react-markdown's default settings. */
export function TextPart({ part }: PartProps) {
  const { markdown } = part.payload as TextPayload;
  return <Markdown>{markdown}</Markdown>;
}

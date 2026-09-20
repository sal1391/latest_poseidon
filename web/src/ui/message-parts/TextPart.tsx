import Markdown from "react-markdown";
import type { PartProps, TextPayload } from "./registry";

/** Renders `text` parts as markdown using react-markdown's default settings. */
export function TextPart({ part }: PartProps) {
  const { markdown } = part.payload as TextPayload;
  return <Markdown>{markdown}</Markdown>;
}

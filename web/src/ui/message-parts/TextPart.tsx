import Markdown from "react-markdown";
import type { PartProps, TextPayload } from "./registry";

/** Renders `text` parts as markdown (react-markdown's defaults) inside the
 * `.prose-poseidon` rule set from globals.css (spec 4). */
export function TextPart({ part }: PartProps) {
  const { markdown } = part.payload as TextPayload;
  return (
    <div className="prose-poseidon">
      <Markdown>{markdown}</Markdown>
    </div>
  );
}

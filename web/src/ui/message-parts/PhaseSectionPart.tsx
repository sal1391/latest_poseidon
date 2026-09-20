import Markdown from "react-markdown";
import type { PartProps, PhaseSectionPayload } from "./registry";

/** Renders `phase_section` parts as a titled, expandable section --
 * `payload.title` as the summary/header, `payload.markdown` rendered the
 * same way `TextPart` renders any other markdown body -- open by default
 * (unlike `ProofPart`'s closed-by-default provenance block: this IS the
 * brief's own narrative content a reader came for, not supplementary
 * evidence to dig into on demand). */
export function PhaseSectionPart({ part }: PartProps) {
  const { title, markdown } = part.payload as PhaseSectionPayload;
  return (
    <details className="rounded-md border border-neutral-200 p-2 dark:border-neutral-800" open>
      <summary className="cursor-pointer font-medium">{title}</summary>
      <div className="mt-1">
        <Markdown>{markdown}</Markdown>
      </div>
    </details>
  );
}

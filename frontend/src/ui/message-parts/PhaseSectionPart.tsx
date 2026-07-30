import Markdown from "react-markdown";
import type { PhaseSectionPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `phase_section` parts as a titled, expandable section --
 * `payload.title` as the summary/header, `payload.markdown` rendered the
 * same way `TextPart` renders any other markdown body -- the same
 * collapsible-details idiom `ProofPart` already uses (doc 01 section 4's
 * own row: "one agent phase ... as an expandable section"), open by
 * default (unlike `ProofPart`'s closed-by-default provenance block: this
 * IS the brief's own narrative content a reader came for, not
 * supplementary evidence to dig into on demand). */
export function PhaseSectionPart({ part }: PartProps) {
  const { title, markdown } = part.payload as PhaseSectionPayload;
  return (
    <details className="phase-section-part" open>
      <summary>{title}</summary>
      <Markdown>{markdown}</Markdown>
    </details>
  );
}

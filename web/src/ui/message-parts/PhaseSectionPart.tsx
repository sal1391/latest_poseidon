import Markdown from "react-markdown";
import { Disclosure } from "../primitives/Disclosure";
import type { PartProps, PhaseSectionPayload } from "./registry";

/** Renders `phase_section` parts as a titled, expandable section --
 * `payload.title` as a heading-2 inside the summary, `payload.markdown`
 * rendered the same way `TextPart` renders any other markdown body -- open
 * by default (unlike `ProofPart`'s closed-by-default provenance block: this
 * IS the brief's own narrative content a reader came for, not supplementary
 * evidence to dig into on demand). */
export function PhaseSectionPart({ part }: PartProps) {
  const { title, markdown } = part.payload as PhaseSectionPayload;
  return (
    <Disclosure open section summary={<h2 className="text-base font-semibold">{title}</h2>}>
      <div className="prose-poseidon">
        <Markdown>{markdown}</Markdown>
      </div>
    </Disclosure>
  );
}

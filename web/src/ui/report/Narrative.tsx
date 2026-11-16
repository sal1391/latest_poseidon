import Markdown from "react-markdown";
import type { ReportNarrative } from "./types";

/** Escape-first, no-links markdown: raw HTML is skipped, anchors and images
 * unwrap to their text (unchanged guards). Heading, prose scope, muted flags
 * and a muted withheld notice per spec 5. */
export function Narrative({ narrative }: { narrative: ReportNarrative }) {
  const md = narrative.grounded && narrative.llm_md ? narrative.llm_md : narrative.template_md;
  const withheld = !narrative.grounded && narrative.llm_md === null;
  return (
    <section className="space-y-2">
      <h4 className="text-base font-semibold">Narrative</h4>
      {withheld ? <p className="text-[13px] text-ink-muted">AI narrative withheld</p> : null}
      <div className="prose-poseidon">
        <Markdown skipHtml disallowedElements={["a", "img"]} unwrapDisallowed>
          {md}
        </Markdown>
      </div>
      {narrative.flags.length > 0 ? (
        <ul className="list-inside list-disc text-[12px] text-ink-muted">
          {narrative.flags.map((flag, i) => (
            <li key={i}>{flag}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

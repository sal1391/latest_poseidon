import Markdown from "react-markdown";
import type { ReportNarrative } from "./types";

/** Escape-first, no-links markdown: raw HTML is skipped, anchors and images unwrap to their text. */
export function Narrative({ narrative }: { narrative: ReportNarrative }) {
  const md = narrative.grounded && narrative.llm_md ? narrative.llm_md : narrative.template_md;
  const withheld = !narrative.grounded && narrative.llm_md === null;
  return (
    <section className="space-y-2 text-sm">
      {withheld ? <p className="font-medium">AI narrative withheld</p> : null}
      <Markdown skipHtml disallowedElements={["a", "img"]} unwrapDisallowed>
        {md}
      </Markdown>
      {narrative.flags.length > 0 ? (
        <ul className="list-inside list-disc text-xs text-neutral-500">
          {narrative.flags.map((flag, i) => (
            <li key={i}>{flag}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

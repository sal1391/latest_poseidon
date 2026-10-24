import type { ReactNode } from "react";

/**
 * The shared `<details>` shell for Proof, Fallback and PhaseSection (spec
 * 4): hairline border, card radius, and a caret that turns when the block
 * opens (the `.disclosure` rules in globals.css). `section` is the roomier
 * variant PhaseSection uses, 12px padding and a plain summary so the title
 * can be a heading; the default is the compact 13px provenance block.
 */
export function Disclosure({
  summary,
  open = false,
  section = false,
  children,
}: {
  summary: ReactNode;
  open?: boolean;
  section?: boolean;
  children: ReactNode;
}) {
  return (
    <details className={`disclosure rounded-card border border-border ${section ? "p-3" : "px-3 py-2"}`} open={open}>
      <summary className={`cursor-pointer ${section ? "" : "text-[13px] font-medium"}`}>{summary}</summary>
      <div className={section ? "mt-2" : "mt-1"}>{children}</div>
    </details>
  );
}

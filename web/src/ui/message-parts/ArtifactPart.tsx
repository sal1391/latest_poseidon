import { Badge } from "../primitives/Badge";
import type { ArtifactPayload, PartProps } from "./registry";

/** Renders `artifact` parts as a download card: a file glyph, the file name
 * linking straight to `url` -- a presigned GET the browser fetches directly,
 * so this needs no backend proxy route -- and a badge naming the MIME type
 * (spec 4). */
export function ArtifactPart({ part }: PartProps) {
  const { name, url, mime } = part.payload as ArtifactPayload;
  return (
    <div className="inline-flex items-center gap-3 rounded-card border border-border px-3 py-2 text-[13px]">
      <svg
        aria-hidden="true"
        className="flex-none text-ink-muted"
        width="20"
        height="20"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      >
        <path d="M5 2.5h7l3.5 3.5v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-13.5a1 1 0 0 1 1-1z" />
        <path d="M12 2.5V6h3.5" />
      </svg>
      <a className="font-medium text-accent hover:underline" href={url} target="_blank" rel="noopener noreferrer" download={name}>
        {name}
      </a>
      <Badge>{mime}</Badge>
    </div>
  );
}

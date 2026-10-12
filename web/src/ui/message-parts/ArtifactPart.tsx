import type { ArtifactPayload, PartProps } from "./registry";

/** Renders `artifact` parts as a download card: the file name links
 * straight to `url` -- a presigned GET the browser fetches directly, so
 * this needs no backend proxy route -- plus a small badge naming the MIME
 * type. */
export function ArtifactPart({ part }: PartProps) {
  const { name, url, mime } = part.payload as ArtifactPayload;
  return (
    <div className="flex items-center gap-2 text-sm">
      <a
        className="font-medium text-blue-600 underline hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        download={name}
      >
        {name}
      </a>
      <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">{mime}</span>
    </div>
  );
}

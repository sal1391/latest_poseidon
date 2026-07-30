import type { ArtifactPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `artifact` parts as a download card: the file name links
 * straight to `url` -- a presigned GET the browser fetches directly (see
 * ArtifactRef's own docstring, poseidon.core.skills.context -- the backend
 * is never in the path of the file's bytes), so this needs no backend proxy
 * route -- plus a small badge naming the MIME type. */
export function ArtifactPart({ part }: PartProps) {
  const { name, url, mime } = part.payload as ArtifactPayload;
  return (
    <div className="artifact-part">
      <a
        className="artifact-link"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        download={name}
      >
        {name}
      </a>
      <span className="artifact-mime">{mime}</span>
    </div>
  );
}

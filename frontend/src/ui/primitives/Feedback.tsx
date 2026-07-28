import { useState } from "react";

export type Verdict = "up" | "down";

export interface FeedbackProps {
  /** The verdict already recorded for this message, if any. */
  verdict?: Verdict;
  onSubmit: (verdict: Verdict, comment?: string) => void;
}

function ThumbIcon({ down = false, filled = false }: { down?: boolean; filled?: boolean }) {
  return (
    <svg
      className={down ? "thumb-icon is-down" : "thumb-icon"}
      viewBox="0 0 24 24"
      width="16"
      height="16"
      aria-hidden="true"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    >
      <path d="M7 21V9.6l4.3-6.3a1.6 1.6 0 0 1 2.9 1L13.3 8.5h5.2a1.8 1.8 0 0 1 1.8 2.2l-1.5 7A2 2 0 0 1 16.8 21H7Z" />
      <path d="M7 21H4.6A1.6 1.6 0 0 1 3 19.4v-8.2a1.6 1.6 0 0 1 1.6-1.6H7" />
    </svg>
  );
}

/**
 * Thumbs up / down chrome for an assistant message (doc 01 §8). Presentation
 * only: the recorded verdict comes in as a prop and every decision goes back
 * out through `onSubmit` — the store owns the state, this owns the prompt.
 */
export function Feedback({ verdict, onSubmit }: FeedbackProps) {
  const [prompting, setPrompting] = useState(false);
  const [comment, setComment] = useState("");

  function submitDown(withComment: boolean) {
    const trimmed = comment.trim();
    onSubmit("down", withComment && trimmed !== "" ? trimmed : undefined);
    setPrompting(false);
    setComment("");
  }

  return (
    <div className="feedback">
      <div className="feedback-row">
        <button
          type="button"
          aria-label="Good response"
          aria-pressed={verdict === "up"}
          onClick={() => {
            setPrompting(false);
            onSubmit("up");
          }}
        >
          <ThumbIcon filled={verdict === "up"} />
        </button>
        <button
          type="button"
          aria-label="Bad response"
          aria-pressed={verdict === "down"}
          onClick={() => setPrompting(true)}
        >
          <ThumbIcon down filled={verdict === "down"} />
        </button>
        {verdict && !prompting ? (
          <span className="feedback-note">Thanks — this helps us tune Poseidon.</span>
        ) : null}
      </div>

      {prompting ? (
        <div className="feedback-prompt">
          <textarea
            className="feedback-comment"
            placeholder="What went wrong?"
            aria-label="What went wrong?"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          <div className="feedback-actions">
            <button type="button" className="btn-primary" onClick={() => submitDown(true)}>
              Send feedback
            </button>
            {/* Dismissing still records the verdict — the comment is optional. */}
            <button type="button" className="btn-quiet" onClick={() => submitDown(false)}>
              Skip
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

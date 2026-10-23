"use client";

import { useState, type FormEvent } from "react";

/**
 * The docked pill composer (spec 3.2). Owns its draft; the page owns the
 * in-flight state and passes it as `disabled`. Submit sends the draft as
 * typed and clears it. The Send button is a filled circle in `--color-send`
 * that mutes to the raised surface while there is nothing to send.
 */
export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const [draft, setDraft] = useState("");
  const blank = draft.trim() === "";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || blank) return;
    const text = draft;
    setDraft("");
    onSend(text);
  }

  return (
    <form
      onSubmit={submit}
      className="mx-auto mb-6 flex w-full max-w-[720px] items-center gap-2 rounded-pill border border-border bg-surface px-2 shadow-1 transition-[border-color,box-shadow] duration-[120ms] focus-within:border-ink-muted focus-within:shadow-2"
    >
      <input
        className="min-w-0 flex-1 bg-transparent px-1 py-3.5 text-[15px] text-ink outline-none placeholder:text-ink-muted"
        aria-label="Message Poseidon"
        placeholder="Message Poseidon…"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button
        type="submit"
        aria-label="Send"
        disabled={disabled || blank}
        className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-full bg-send text-accent-ink transition-opacity duration-[120ms] disabled:bg-surface-raised disabled:text-ink-muted"
      >
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 13V3M4 7l4-4 4 4" />
        </svg>
      </button>
    </form>
  );
}

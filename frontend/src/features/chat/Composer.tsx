import type { RefObject } from "react";
import { SkillsPicker } from "./SkillsPicker";

export interface ComposerProps {
  /** Input value, owned by ChatScreen so chips and skills can seed it. */
  value: string;
  onChange: (value: string) => void;
  /** Replace the draft with a starter template (skills picker, flow chips). */
  onInsert: (text: string) => void;
  onSubmit: (text: string) => void;
  /** True while the active conversation is streaming. */
  disabled?: boolean;
  inputRef?: RefObject<HTMLInputElement>;
}

export function Composer({
  value,
  onChange,
  onInsert,
  onSubmit,
  disabled = false,
  inputRef,
}: ComposerProps) {
  function submit() {
    if (!disabled) onSubmit(value);
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <SkillsPicker onPick={onInsert} />
      <input
        ref={inputRef}
        placeholder="Message Poseidon…"
        aria-label="Message Poseidon"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // Enter sends, Shift+Enter is left for a future multi-line composer.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <button
        type="submit"
        className="send-button"
        aria-label="Send"
        disabled={disabled || value.trim() === ""}
      >
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 19V5M5 12l7-7 7 7" />
        </svg>
      </button>
    </form>
  );
}

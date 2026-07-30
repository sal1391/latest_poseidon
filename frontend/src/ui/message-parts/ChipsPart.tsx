import type { ChipsPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `chips` parts as a row of option buttons. Clicking one sends its
 * `send_text` -- falling back to its `label` when absent -- as a user
 * message through the same send path the composer uses (ChatScreen's own
 * `onChipSelect` wiring) -- disabled whenever the payload itself says so,
 * OR while a send for this conversation is already in flight (ChatScreen's
 * `sendingFor` gate, threaded down as `disabled`).
 *
 * `send_text` is not scoped to any one chip kind (P8 whole-branch
 * final-review wave, 2026-07-30, item 9 / M-6 -- corrects this comment's
 * own earlier "clarification chips only" claim, now false): orchestrator.
 * py's own `_finish_clarify` sends a "for <name>" cue on clarification
 * chips, and `api/live_chat.py`'s opener chips carry the pinned D19 entry
 * phrase the same way. This component stays agnostic either way -- the
 * `?? label` fallback exists for whatever chip payload carries no
 * `send_text` at all, with no branching here or in ChatScreen either
 * time. */
export function ChipsPart({ part, onChipSelect, disabled }: PartProps) {
  const { options, disabled: payloadDisabled } = part.payload as ChipsPayload;
  const isDisabled = disabled === true || payloadDisabled === true;
  return (
    <>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className="chip"
          disabled={isDisabled}
          onClick={() => onChipSelect?.(option.id, option.send_text ?? option.label)}
        >
          {option.label}
        </button>
      ))}
    </>
  );
}

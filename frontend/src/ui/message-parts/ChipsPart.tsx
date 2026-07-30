import type { ChipsPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `chips` parts as a row of option buttons. Clicking one sends its
 * `send_text` -- falling back to its `label` when absent -- as a user
 * message through the same send path the composer uses (ChatScreen's own
 * `onChipSelect` wiring) -- disabled whenever the payload itself says so,
 * OR while a send for this conversation is already in flight (ChatScreen's
 * `sendingFor` gate, threaded down as `disabled`).
 *
 * `send_text` (final-review wave item 2) is SCOPED to clarification chips
 * only: orchestrator.py's own `_finish_clarify` sends a "for <name>" cue so
 * the deterministic parser resolves the customer on the click, but the
 * opener's flow chips carry no `send_text` at all, so they keep sending
 * their bare label unchanged -- this fallback is what makes that scoping
 * live entirely in the payload, with no branching here or in ChatScreen. */
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

import type { ChipsPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** Renders `chips` parts as a row of option buttons. Clicking one sends its
 * label as a user message through the same send path the composer uses
 * (ChatScreen's own `onChipSelect` wiring) -- disabled whenever the payload
 * itself says so, OR while a send for this conversation is already in
 * flight (ChatScreen's `sendingFor` gate, threaded down as `disabled`). */
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
          onClick={() => onChipSelect?.(option.id, option.label)}
        >
          {option.label}
        </button>
      ))}
    </>
  );
}

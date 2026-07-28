import type { ChipsPayload } from "../../api/types";
import type { PartProps } from "./registry";

type Payload = ChipsPayload & { disabled?: boolean };

/** Renders `chips` parts as a row of option buttons. */
export function ChipsPart({ part, onChipSelect }: PartProps) {
  const { options, disabled } = part.payload as Payload;
  return (
    <>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className="chip"
          disabled={disabled === true}
          onClick={() => onChipSelect?.(option.id, option.label)}
        >
          {option.label}
        </button>
      ))}
    </>
  );
}

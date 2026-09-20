import type { ChipsPayload, PartProps } from "./registry";

/** Renders `chips` parts as a row of option buttons. Clicking one sends its
 * `send_text` -- falling back to its `label` when absent -- through
 * `onChipSelect`, disabled whenever the payload itself says so, OR while a
 * send for this conversation is already in flight (threaded down as the
 * `disabled` prop). */
export function ChipsPart({ part, onChipSelect, disabled }: PartProps) {
  const { options, disabled: payloadDisabled } = part.payload as ChipsPayload;
  const isDisabled = disabled === true || payloadDisabled === true;
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className="rounded-full border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
          disabled={isDisabled}
          onClick={() => onChipSelect?.(option.id, option.send_text ?? option.label)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

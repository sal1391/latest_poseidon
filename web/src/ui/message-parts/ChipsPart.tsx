import type { ChipsPayload, PartProps } from "./registry";

/** Renders `chips` parts as a row of pill buttons. Clicking one sends its
 * `send_text` -- falling back to its `label` when absent -- through
 * `onChipSelect`, disabled whenever the payload itself says so, OR while a
 * send for this conversation is already in flight (threaded down as the
 * `disabled` prop). A disabled chip fades rather than greys out, so a
 * replayed turn's chips still read (spec 4). */
export function ChipsPart({ part, onChipSelect, disabled }: PartProps) {
  const { options, disabled: payloadDisabled } = part.payload as ChipsPayload;
  const isDisabled = disabled === true || payloadDisabled === true;
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className="rounded-full border border-border bg-surface px-3 py-1 text-[13px] transition-colors duration-[120ms] enabled:hover:bg-surface-raised disabled:cursor-default disabled:opacity-60"
          disabled={isDisabled}
          onClick={() => onChipSelect?.(option.id, option.send_text ?? option.label)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

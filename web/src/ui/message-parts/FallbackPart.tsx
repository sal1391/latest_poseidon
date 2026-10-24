import { Disclosure } from "../primitives/Disclosure";
import type { PartProps } from "./registry";

/** Safe fallback for any part kind the registry doesn't recognize yet: the
 * kind as the summary, the payload as JSON in the mono font (spec 4). */
export function FallbackPart({ part }: PartProps) {
  const { kind, payload } = part;
  return (
    <Disclosure summary={kind}>
      <pre className="overflow-x-auto font-mono text-[12px]">{JSON.stringify(payload, null, 2)}</pre>
    </Disclosure>
  );
}

import type { MessagePart } from "./message-parts";

/**
 * Python's `_build_opener_parts()` (backend/poseidon/core/chat/history.py),
 * transcribed. The D19 entry phrases are pinned on the Python side
 * (orchestrator.py ENTRY_PHRASE_EXISTING / ENTRY_PHRASE_PROSPECT) and by
 * contracts/turns/opener.json, which opener.test.ts compares against.
 */
export const OPENER_PARTS: MessagePart[] = [
  { kind: "text", payload: { markdown: "Ask about your data, or pick a flow:" } },
  {
    kind: "chips",
    payload: {
      options: [
        { id: "existing_customer", label: "Existing customer", send_text: "start an existing-customer brief" },
        { id: "new_prospect", label: "New customer prospect", send_text: "start a new-prospect brief" },
      ],
    },
  },
];

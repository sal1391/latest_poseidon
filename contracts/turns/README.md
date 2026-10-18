# Turn contract corpus

One JSON file per turn scenario, plus `opener.json`. Each scenario file holds the ordered SSE
frames a real turn emitted (`{"event", "data"}`), the assistant `parts` the public chat route would
have persisted by folding those same frames, and the turn's `terminal` frame (`done` or `error`).
`opener.json` holds the parts a brand-new conversation opens with.

These files are the one shared contract the Python streamer and the Next.js translator both pin, so
the two sides cannot drift apart silently. They are generated, never hand-edited.

Regenerate (from `backend/`, PowerShell — set the variable, run, then unset it):

    $env:POSEIDON_UPDATE_CONTRACTS="1"
    .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts -q
    Remove-Item Env:\POSEIDON_UPDATE_CONTRACTS

Producer: `backend/tests/contracts/scenarios.py` + `backend/tests/contracts/test_turn_contracts.py`
(bash equivalent: `POSEIDON_UPDATE_CONTRACTS=1 .venv/Scripts/python.exe -B -m pytest ...`).

Changes only land with a regenerated fixture in the same commit and a reviewer reading the diff: an
unexplained diff here is a contract change, not a formatting one.

Nothing in these files is normalised. Every scenario runs offline against fixed ids (`turn-1`/
`msg-1`, and `turn-2`/`msg-2` for the replay scenario's retry) and a fixed reference date, so no
uuid, clock, presigned URL or database value ever reaches a frame. If a future scenario introduces
one, normalise it in `scenarios.to_json` and list it here.

The two report scenarios (`report_entry`, `report_turn`) are the exception to "no clock, no uuid":
`report_turn` pins `run_id` (`skill.uuid7`), `generated_at` (`skill._now`), the period window
(`skill._today`), every `queries[].ms` (the report frames module's `time.perf_counter`, one
millisecond per query) and the PDF bytes (`render.render_pdf`) through those five monkeypatched
hooks, so the golden is byte-stable. A change to any of those hooks or their fixed values is a
contract change.

# Router-case candidates

This directory holds YAML files produced by
`python -m poseidon.scripts.export_router_cases` -- one file per harvested
chat turn, named `<turn_id>.yml`. Each file looks like this:

```yaml
# Router-case candidate -- harvested by export_router_cases.py.
# Human-review promotion workflow: see README.md in this directory.
# source turn_id: 0f2b6b4a-...
# source trace_id: 9c1a7e2f...
question: "What was GP for Maersk in April 2026?"
expected: TODO-human-review
```

## Why the contents are gitignored but this README is not

A candidate file is harvested from real usage: `question` is a real user's
message, verbatim. That text may carry customer names, real figures, or
other content that has no business sitting in git history. The `.yml`
files this directory holds are therefore excluded by the repository's
`.gitignore` (see the root `.gitignore`'s own comment next to the rule).
This README is a normal tracked file precisely so the directory itself,
and the promotion workflow below, survive a fresh checkout even though it
starts out empty of candidates.

## The human-review promotion workflow

A candidate is deliberately **not** a runnable test fixture yet. It is
missing everything that makes a case in
`backend/tests/routing_cases.yml` (the P5 router-decision suite) actually
mean something: nobody has decided what the router *should* have decided
for this question. Promoting a candidate means:

1. Read the `question` field and decide whether this turn is worth a
   permanent regression case (a real routing decision worth locking in,
   an interesting edge case, a past bug's repro, etc.). Most harvested
   candidates are not -- that is expected, not a problem to fix.
2. If it is worth keeping, hand-author a new entry in
   `backend/tests/routing_cases.yml` using that file's own format:
   `id`, `user` (the candidate's `question`, or a cleaned-up version of
   it), `expect` (the *correct* skill id and a minimal `args_subset`,
   replacing this file's `expected: TODO-human-review` placeholder), and
   an `execution` block (`stub_script`, when the expected skill is
   registered and enabled, or `live_only: true` with a `reason`
   otherwise). See `backend/tests/test_llm_loop.py` for exactly how those
   fields are consumed.
3. Delete the candidate file once it has been promoted (or once you have
   decided it is not worth promoting). Nothing reads this directory's
   contents automatically -- an un-promoted candidate sitting here forever
   is inert, but also no longer useful evidence of anything once it is
   stale.

## Producing candidates

```
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \
    python -m poseidon.scripts.export_router_cases \
    --since 2026-08-01T00:00:00Z --limit 20 [--include-errors]
```

`kind='memory_update'` turns and redacted turns are never exported.
`status='error'` turns are excluded unless `--include-errors` is given.
Deployed usage runs this under `poseidon_admin` role membership (see the
script's own module docstring) -- it is the only way to see every user's
turns, not only the operator's own.

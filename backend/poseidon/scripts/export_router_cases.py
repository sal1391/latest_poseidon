"""Harvest real chat turns into candidate router-decision fixtures (Phase 11
Task 4, doc 06 observability): the human-in-the-loop half of the router-
evidence loop this phase's earlier tasks opened. T1 gave the ``poseidon_
admin`` role read access to every user's ``turn_run`` row; this script is
the first thing that actually USES that access to turn real usage into
material for ``backend/tests/routing_cases.yml`` (the P5 router-decision
suite).

**Output is CANDIDATE-only, never a suite fixture directly.** One YAML file
per selected turn lands in ``backend/tests/router_cases/candidates/`` (see
that directory's own ``README.md`` for the full human-review promotion
workflow), holding exactly two data fields -- ``question`` (the turn's own
text, verbatim) and ``expected: TODO-human-review`` (a literal placeholder,
never a guess at what the router should have decided) -- plus the source
``turn_id``/``trace_id`` as YAML COMMENTS, not data fields. This is
deliberately a DIFFERENT, simpler shape than ``routing_cases.yml``'s own
``id``/``user``/``expect``/``execution`` schema: nobody has reviewed a
harvested question yet, so there is no ``expect``/``execution`` block this
script could honestly fill in on a human's behalf. "Loads through the
router-suite loader" (this task's own test suite, ``test_harvest_cost.
py``) proves YAML/byte-convention compatibility with ``routing_cases.
yml``'s own file, not schema identity with its cases.

**Exclusions.** ``kind='memory_update'`` rows are never candidates -- a
background memory-consolidation run has no question a human ever typed,
and (by construction) usually no ``question`` text at all. Redacted rows
(``redacted_at IS NOT NULL``) are never candidates either: doc 05 section
7's deletion contract already nulled their ``question`` out, and a
harvest tool has no business resurrecting deleted content even if it
somehow could. Both exclusions are UNCONDITIONAL -- neither is affected by
``--include-errors``. A ``status='error'`` turn IS excluded by default (an
errored turn's own routing decision, if any, is unproven), but
``--include-errors`` lifts exactly that one filter, on the theory that a
bad outcome is sometimes exactly the interesting case to build a
regression fixture from.

**Ordering: oldest-since-cutoff first, not newest-first.** Mirrors ``core/
chat/history.py``'s own ``ORDER BY created_at ASC`` convention for
sequential, paginate-forward consumption (as opposed to that same module's
``updated_at DESC`` for the conversations SIDEBAR, a "what's recent"
convenience view this tool is not). An operator advancing ``--since`` to
the last-seen turn's ``created_at`` after each run walks forward through
every candidate exactly once, never silently skipping the ones between two
runs the way a DESC "give me the newest N" ordering could when more than
``--limit`` turns arrive between harvests.

**Operator posture (no new auth code).** Both this script and its sibling
``cost_rollup.py`` open every query inside ``poseidon.core.db.
rls_transaction`` with ``app_role="poseidon_admin"`` -- migration 0005's
named, NOLOGIN, SELECT-only role, granted to human operators out of band
(doc 05 section 7: "granted to named operators, never to the application's
runtime role"). Locally, this dev compose database's own ``DATABASE_URL``
role is the cluster superuser, which unconditionally bypasses row-level
security regardless of any ``SET LOCAL ROLE`` this script issues (``core/
db.py``'s own module docstring, "round-0 correction") -- so the admin role
switch is inert here, not load-bearing. DEPLOYED usage is different: a real
environment's ``DATABASE_URL`` authenticates as an ordinary, non-privileged
role, and this script only sees every user's rows there because an
operator has separately granted that connecting role membership in
``poseidon_admin`` (the identical membership-grant step ``test_runlog_rls.
py``'s own ``test_admin_role_can_read_across_users_via_set_role`` proves
functionally) -- a per-environment operational step, deliberately outside
this script's own scope.

**Environment.** Reads ``DATABASE_URL`` straight from the process
environment (never ``poseidon.core.config.Settings`` -- this script needs
nothing else Settings validates, matching ``seed_synthetic.py``'s/
``demo_query.py``'s own minimal-footprint convention for this package).

Usage::

    DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
        python -m poseidon.scripts.export_router_cases \\
        --since 2026-08-01T00:00:00Z --limit 20 [--include-errors]
"""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.db import build_engine, rls_transaction

# poseidon_admin (migration 0005): the named, SELECT-only, cross-user read
# role every operator tool in this phase runs under -- see the module
# docstring's "Operator posture" section.
_ADMIN_ROLE = "poseidon_admin"
# Inert under the admin policy's own USING (true) predicate (it never reads
# app.user_sub) -- a fixed, readable label rather than a real identity,
# since this script has no caller identity of its own to carry.
_SCRIPT_USER_SUB = "poseidon-admin-script:export_router_cases"

# backend/poseidon/scripts/export_router_cases.py -> backend/tests/
# router_cases/candidates -- resolved from this file's own location so the
# target directory is correct regardless of the operator's current working
# directory when `python -m poseidon.scripts.export_router_cases` runs.
_CANDIDATES_DIR = Path(__file__).resolve().parents[2] / "tests" / "router_cases" / "candidates"

_NO_TRACE_ID = "(none)"

# {status_clause} is one of two fixed, non-user-controlled literals below --
# never interpolated user input -- so this stays a plain str.format, not a
# second bind parameter.
_SELECT_SQL = """
    SELECT id, question, trace_id
    FROM turn_run
    WHERE kind = 'chat_turn'
      AND redacted_at IS NULL
      AND created_at >= :since
      {status_clause}
    ORDER BY created_at ASC
    LIMIT :limit
"""
_EXCLUDE_ERRORS_CLAUSE = "AND status <> 'error'"


def _select_sql(include_errors: bool):
    clause = "" if include_errors else _EXCLUDE_ERRORS_CLAUSE
    return text(_SELECT_SQL.format(status_clause=clause))


def _parse_since(value: str) -> datetime:
    """ISO-8601 -> an aware UTC ``datetime``. A value with no offset is
    treated as UTC explicitly (this codebase's own UTC-first convention --
    see ``core/obs.py``'s ``_iso8601_utc``) rather than left naive, which
    psycopg would otherwise adapt using the SESSION's timezone, not
    necessarily UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m poseidon.scripts.export_router_cases",
        description="Harvest real chat turns into candidate router-decision "
        "fixture YAML for human review (backend/tests/router_cases/candidates/).",
    )
    parser.add_argument(
        "--since",
        required=True,
        type=_parse_since,
        metavar="<iso>",
        help="ISO-8601 lower bound on turn_run.created_at (e.g. 2026-08-01T00:00:00Z)",
    )
    parser.add_argument(
        "--limit",
        required=True,
        type=int,
        metavar="N",
        help="maximum number of candidate turns to export",
    )
    parser.add_argument(
        "--include-errors",
        action="store_true",
        help="also export status='error' turns (excluded by default)",
    )
    return parser.parse_args(argv)


def _yaml_question_line(question: str) -> str:
    """``question: "<escaped text>"`` on one line, with a BARE (unquoted)
    key -- matching ``routing_cases.yml``'s own ``user: "..."``/``id:
    existing_brief_by_name`` convention of quoting free-text VALUES only,
    never keys. Dumping ``{"question": question}`` directly (rather than
    hand-writing the ``"question: "`` prefix) would quote the KEY too --
    ``default_style`` forces double-quoted style onto every plain scalar
    PyYAML emits, keys included -- so only the VALUE is run through
    ``yaml.safe_dump`` (confirmed empirically to emit a clean one-line
    scalar with no stray document-end marker, unlike some other YAML
    dumpers' handling of a bare top-level scalar) and the key is a literal.
    ``allow_unicode=False`` makes PyYAML itself escape any non-ASCII
    codepoint as ``\\uXXXX`` (this codebase's own ASCII-on-disk discipline,
    extended here to generated data, not just source); a huge ``width``
    keeps one question on one physical line regardless of length, rather
    than PyYAML's default ~80-column wrap."""
    quoted_value = yaml.safe_dump(
        question, allow_unicode=False, default_style='"', width=10**6
    ).rstrip("\n")
    return f"question: {quoted_value}"


def _candidate_text(turn_id: str, question: str, trace_id: str | None) -> str:
    """The full candidate file body -- see the module docstring for why
    ``question``/``expected`` are the only two DATA fields, and the turn id
    /trace id are comments, not fields."""
    lines = [
        "# Router-case candidate -- harvested by export_router_cases.py.",
        "# Human-review promotion workflow: see README.md in this directory.",
        f"# source turn_id: {turn_id}",
        f"# source trace_id: {trace_id if trace_id is not None else _NO_TRACE_ID}",
        _yaml_question_line(question),
        "expected: TODO-human-review",
    ]
    return "\n".join(lines) + "\n"


def _export(engine: Engine, *, since: datetime, limit: int, include_errors: bool) -> int:
    _CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    with rls_transaction(engine, _SCRIPT_USER_SUB, app_role=_ADMIN_ROLE) as conn:
        rows = conn.execute(_select_sql(include_errors), {"since": since, "limit": limit}).all()

    for turn_id, question, trace_id in rows:
        turn_id_str = str(turn_id)
        content = _candidate_text(turn_id_str, question or "", trace_id)
        (_CANDIDATES_DIR / f"{turn_id_str}.yml").write_text(content, encoding="utf-8")

    return len(rows)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    url = os.environ.get("DATABASE_URL", "")
    if not url.strip():
        print(
            "DATABASE_URL is required to export router-case candidates "
            "(e.g. postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon)",
            file=sys.stderr,
        )
        return 2

    engine = build_engine(url)
    try:
        count = _export(
            engine, since=args.since, limit=args.limit, include_errors=args.include_errors
        )
    finally:
        engine.dispose()

    print(f"exported {count} candidate(s) to {_CANDIDATES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Verdict-rate roll-up (Phase 12 Task 3, doc 06 section 7 / D25): the
statistical half of this phase's harvest loop -- ``export_router_cases.py``
(this script's sibling, extended by the same task) turns individual
thumbs-down verdicts into router-decision candidates for human review; this
script turns ALL verdicts, up and down together, into a per-dimension rate
an operator can watch trend over time.

**One mode, one shape.** ``--by {skill,role,prompt_version}`` prints one
JSON line per group -- ``{"group", "up", "down", "down_rate"}`` -- exactly
the shape the plan pins. There is no second mode the way ``cost_rollup.py``
has ``--spike-check``: a verdict rate has no analogous "flag the outliers"
operation this phase's brief asks for.

**Skill attribution, disclosed (the plan's own deferred lookup).** A real
turn's ROUTED skill is recorded on ``tool_calls.tool`` -- literally
``ToolRecord.skill_id`` (``core/llm/loop.py``), written by ``core/chat/
orchestrator.py``'s ``_append_records`` as ``tool=record.skill_id`` for
every dispatch of the turn. ``turn_run.parsed`` was the other candidate the
plan named and is NOT it: that column holds ``_parsed_to_loggable_dict``'s
output -- the turn's deterministically-parsed slots/entities (customer,
periods, mode) -- and carries no skill-id field at all, confirmed by
reading ``orchestrator.py`` directly rather than assumed. ``--by role`` and
``--by prompt_version`` read ``llm_calls.role``/``llm_calls.prompt_version``
instead -- the same two columns ``cost_rollup.py`` already groups by for
spend, now grouped for verdicts.

**Per-VERDICT attribution, not per-call -- the one place this script's
grouping deliberately differs from ``cost_rollup.py``'s.** ``cost_rollup.py``
groups ``llm_calls`` rows directly, one row per group membership, because
each row is its OWN independent spend. A verdict is not: it is cast ONCE per
message (``message_feedback``'s own ``UNIQUE (message_id, user_sub)``), but
the turn behind that message can carry MORE than one ``llm_calls`` row (the
agent loop iterates route -> tool -> route again -> end_turn -- confirmed
directly against this environment's own compose Postgres while validating
this task: a genuine turn recorded two ``role='router'`` rows) or more than
one ``tool_calls`` row (a self-correction retry, or -- rarer still -- two
differently-named skills in one turn; see ``orchestrator.py``'s own module
docstring). Joining ``message_feedback`` straight to either child table and
counting rows would count that ONE verdict once per child row instead of
once per verdict, inflating both ``up``/``down`` and silently corrupting
``down_rate``. ``COUNT(DISTINCT mf.id) FILTER (...)`` is what keeps a
verdict counted exactly once per group it genuinely belongs to, regardless
of how many child rows its turn happens to have.

**A verdict whose turn has no matching child row contributes to no group
for that dimension -- INNER JOIN, not LEFT.** A clarify turn (``core/chat/
orchestrator.py``'s ``_finish_clarify``) resolves entity ambiguity through
the deterministic parser alone, dispatching neither an LLM call nor a
skill -- but it DOES produce a message, so it CAN receive feedback. Such a
verdict simply does not appear under ``--by skill``, ``--by role``, or
``--by prompt_version``: there is no dispatch to attribute it to, the same
"a turn contributes to a dimension only when it actually has a row of that
kind" rule ``cost_rollup.py``'s own per-call grouping already lives by for
spend, applied here to counts of verdicts instead of sums of tokens.

**Redacted turns still count -- the opposite rule from ``export_router_
cases.py``'s thumbs-down path, the SAME rule ``cost_rollup.py`` already
applies to spend.** Doc 05 section 7's redaction contract nulls ``turn_run.
question``/``answer_summary``/``parsed`` and ``tool_calls.args``/``result_
digest`` -- CONTENT, not the fact that a verdict was cast. ``tool_calls.
tool``/``status`` and ``llm_calls.role``/``prompt_version``/``status`` are
all untouched by redaction (``runlog.py``'s own ``_REDACT_TOOL_CALLS_SQL``
never mentions them), so a redacted turn's verdict keeps contributing to
every rate it always did -- unlike the harvest exporter, this script exposes
no verbatim content, only aggregate counts, so there is nothing here for
redaction's privacy contract to protect against.

**``--by`` defaults to ``"skill"`` when omitted (disclosed judgment call,
mirroring ``cost_rollup.py``'s own default-``"day"`` disclosure).** The plan
brackets ``--by`` as optional without naming a default; "skill" is chosen as
the most actionable bare-minimum view for the question this script
primarily exists to answer -- "which skill is generating bad answers" --
over the router's own configuration (role/prompt_version), which changes far
less often.

**``down_rate`` is rounded to 4 decimal places (disclosed judgment call).**
Every group here has ``up + down >= 1`` by construction (a group only
exists because ``GROUP BY`` found at least one matching feedback row, and
``verdict`` is CHECK-constrained to ``'up'``/``'down'`` -- there is no third
value to leave the denominator at zero), so the rounding is purely cosmetic:
it exists only to keep e.g. ``2/3`` from printing as ``0.6666666666666666``
on every line an operator has to read.

**Environment -- via raw ``os.environ``, unlike ``cost_rollup.py``'s
``Settings``.** This script reads ``DATABASE_URL`` straight from the
process environment, mirroring ``export_router_cases.py``'s own identical
choice (see that script's own module docstring): unlike ``cost_rollup.py``,
which needs ``Settings.token_spike_threshold`` for its second mode, nothing
here reads any OTHER ``Settings`` field, so requiring a full ``Settings``
construction (and therefore ``S3_BUCKET``, etc., for a bespoke invocation)
would add a real operational cost for zero benefit.

**Operator posture (no new auth code) -- identical to both siblings.** Every
query opens inside ``poseidon.core.db.rls_transaction`` with
``app_role="poseidon_admin"``. See ``export_router_cases.py``'s own module
docstring for the full rationale (local superuser bypass vs. deployed
membership grant); not repeated here.

Usage::

    DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
        python -m poseidon.scripts.feedback_rollup \\
        --by skill --since 2026-08-01T00:00:00Z
        python -m poseidon.scripts.feedback_rollup --by role
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.db import build_engine, rls_transaction

_ADMIN_ROLE = "poseidon_admin"
_SCRIPT_USER_SUB = "poseidon-admin-script:feedback_rollup"

# {table}/{column} -- the child table/column each dimension actually groups
# by; see the module docstring's "Skill attribution, disclosed" section for
# why "skill" reads tool_calls.tool rather than turn_run.parsed.
_BY_CHOICES = ("skill", "role", "prompt_version")
_GROUP_SOURCE = {
    "skill": ("tool_calls", "tool"),
    "role": ("llm_calls", "role"),
    "prompt_version": ("llm_calls", "prompt_version"),
}

# COUNT(DISTINCT mf.id) FILTER (...) -- not a plain COUNT(*) FILTER -- is
# what keeps one verdict from being counted once per child row when its
# turn has more than one (see the module docstring's "Per-VERDICT
# attribution" section). The join is INNER on purpose (see "A verdict whose
# turn has no matching child row" in the module docstring).
_ROLLUP_SQL = """
    SELECT c.{column} AS grp,
           COUNT(DISTINCT mf.id) FILTER (WHERE mf.verdict = 'up') AS up,
           COUNT(DISTINCT mf.id) FILTER (WHERE mf.verdict = 'down') AS down
    FROM message_feedback mf
    JOIN {table} c ON c.turn_run_id = mf.run_id
    {since_clause}
    GROUP BY c.{column}
    ORDER BY c.{column}
"""
_ROLLUP_SINCE_CLAUSE = "WHERE mf.created_at >= :since"


def _parse_since(value: str) -> datetime:
    """See ``export_router_cases.py``'s identical helper for the full
    rationale (duplicated rather than shared -- the same convention
    ``cost_rollup.py`` already established for this exact helper, applied
    to a third independent script entry point)."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m poseidon.scripts.feedback_rollup",
        description="Verdict-rate roll-up (up/down/down_rate) per skill, "
        "role, or prompt_version, over message_feedback joined to its "
        "turn's tool/LLM calls.",
    )
    parser.add_argument(
        "--by",
        choices=_BY_CHOICES,
        default="skill",
        help="grouping dimension for the roll-up (default: skill)",
    )
    parser.add_argument(
        "--since",
        default=None,
        type=_parse_since,
        metavar="<iso>",
        help="ISO-8601 lower bound on message_feedback.created_at (default: no lower bound)",
    )
    return parser.parse_args(argv)


def _rollup(engine: Engine, *, by: str, since: datetime | None) -> list[dict]:
    table, column = _GROUP_SOURCE[by]
    since_clause = _ROLLUP_SINCE_CLAUSE if since is not None else ""
    sql = text(_ROLLUP_SQL.format(table=table, column=column, since_clause=since_clause))
    params = {"since": since} if since is not None else {}

    with rls_transaction(engine, _SCRIPT_USER_SUB, app_role=_ADMIN_ROLE) as conn:
        rows = conn.execute(sql, params).all()

    result = []
    for grp, up, down in rows:
        total = up + down
        # total is always >= 1 here -- see the module docstring's "down_rate
        # is rounded" section for why the guard below is unreachable, kept
        # only as a defensive fallback rather than a bare division.
        down_rate = round(down / total, 4) if total else 0.0
        result.append({"group": grp, "up": up, "down": down, "down_rate": down_rate})
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    url = os.environ.get("DATABASE_URL", "")
    if not url.strip():
        print(
            "DATABASE_URL is required to roll up feedback verdicts "
            "(e.g. postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon)",
            file=sys.stderr,
        )
        return 2

    engine = build_engine(url)
    try:
        for group in _rollup(engine, by=args.by, since=args.since):
            print(json.dumps(group))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

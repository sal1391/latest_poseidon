"""LLM cost roll-up and token-spike check (Phase 11 Task 4, doc 06
observability): the spend-accounting half of the router-evidence loop this
phase's earlier tasks opened (T1's ``poseidon_admin`` read role is what
lets this script see every user's calls, not only its own caller's).

**Two modes, one script.** ``--by {user,day,model,role,prompt_version}``
prints one JSON line per group -- ``{"group", "calls", "input_tokens",
"output_tokens"}``, exactly the shape the brief pins -- summed over
``llm_calls`` directly. ``--spike-check`` instead lists ``turn_run`` rows
whose ``input_tokens + output_tokens`` exceeds ``Settings.
token_spike_threshold``.

**Per-call attribution, by construction, not by extra code.** Every
roll-up query groups ``llm_calls`` rows directly -- never joined to, or
summed at, ``turn_run`` -- because ``turn_run`` itself carries no
``model_id``/``role``/``prompt_version`` column to group by (doc 06
section 1's own schema, decision D27: "per-call, never summed into the
parent until ``RunLogWriter.finalize`` rolls the totals up"). A multi-call
turn's calls therefore land in as many different groups as they have
distinct role/model/prompt_version values, each contributing its own
tokens independently -- there is no code path that could accidentally
collapse them into one per-turn bucket, because no such bucket exists at
this granularity.

**``kind='memory_update'`` and redacted turns are both counted as real
spend, on purpose -- the opposite rule from this script's sibling,
``export_router_cases.py``.** Neither the per-dimension roll-up nor
``--spike-check`` filters on ``turn_run.kind`` or ``redacted_at`` at all:
a background memory-consolidation run still burned real model tokens, and
``RunLogWriter.redact_turns_for_conversation`` deliberately never touches
``llm_calls`` (doc 05 section 7 names it "untouched by design" -- redaction
erases CONTENT, never the fact that money was spent). Extending "real
spend" from the roll-up (the brief's own explicit rule) to ``--spike-check``
too (this script's own disclosed judgment call, not separately pinned) is
the same reasoning applied to both of this script's two modes rather than
carved out for only one of them.

**"day" is a UTC calendar date, not the Postgres session's local day.**
``(created_at AT TIME ZONE 'UTC')::date`` fixes the day boundary
independently of whatever ``TimeZone`` setting a given connection happens
to have, matching this codebase's UTC-first convention elsewhere (``core/
obs.py``'s ``_iso8601_utc``).

**``--by`` defaults to ``"day"`` when omitted (disclosed judgment call).**
The brief brackets both ``--by`` and ``--since`` as optional but does not
name a default grouping; "day" is chosen as the most useful bare-minimum
view of spend over time an operator would reach for first.

**``--spike-check`` with ``token_spike_threshold <= 0`` short-circuits
BEFORE opening any database connection at all** -- not merely before
querying. 0 (the field's own default) means "off"; a stray negative value
is treated the same way (there is no sense in which a negative threshold
is "on"). This is what lets the disabled path exit 0 even against an
unreachable ``DATABASE_URL`` (proven directly, not just asserted, by
``test_harvest_cost.py``).

**Operator posture (no new auth code) -- identical to ``export_router_
cases.py``.** Every query opens inside ``poseidon.core.db.rls_transaction``
with ``app_role="poseidon_admin"``. See that script's own module docstring
for the full rationale (local superuser bypass vs. deployed membership
grant); not repeated here.

**Environment -- via ``Settings``, unlike ``export_router_cases.py``'s raw
``os.environ`` read.** This script is the one caller of
``Settings.token_spike_threshold`` (a real, typed, centrally-documented
config field, not a second, parallel env-var convention this script would
otherwise have to invent) -- so it constructs a real ``Settings`` via
``get_settings()``, which also validates ``DATABASE_URL``/``S3_BUCKET``/
etc. as a side effect. A deployed environment already satisfies that full
contract (the backend server needs it too); only a bespoke, backend-less
invocation must additionally export ``S3_BUCKET`` (any non-blank value --
this script never reads it) purely to satisfy ``Settings`` construction.

Usage::

    DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
    S3_BUCKET=poseidon-artifacts TOKEN_SPIKE_THRESHOLD=20000 \\
        python -m poseidon.scripts.cost_rollup --by role --since 2026-08-01T00:00:00Z
        python -m poseidon.scripts.cost_rollup --spike-check --since 2026-08-01T00:00:00Z
"""

import argparse
import json
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.config import get_settings
from poseidon.core.db import build_engine, rls_transaction

_ADMIN_ROLE = "poseidon_admin"
_SCRIPT_USER_SUB = "poseidon-admin-script:cost_rollup"

_BY_CHOICES = ("user", "day", "model", "role", "prompt_version")
_GROUP_EXPR = {
    "user": "user_sub",
    "day": "(created_at AT TIME ZONE 'UTC')::date",
    "model": "model_id",
    "role": "role",
    "prompt_version": "prompt_version",
}

_ROLLUP_SQL = """
    SELECT {group_expr} AS grp, COUNT(*) AS calls,
           SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens
    FROM llm_calls
    {since_clause}
    GROUP BY {group_expr}
    ORDER BY {group_expr}
"""
_ROLLUP_SINCE_CLAUSE = "WHERE created_at >= :since"

_SPIKE_SQL = """
    SELECT id, user_sub, kind, input_tokens, output_tokens,
           (input_tokens + output_tokens) AS total_tokens
    FROM turn_run
    WHERE (input_tokens + output_tokens) > :threshold
      {since_clause}
    ORDER BY total_tokens DESC
"""
_SPIKE_SINCE_CLAUSE = "AND created_at >= :since"


def _parse_since(value: str) -> datetime:
    """See ``export_router_cases.py``'s identical helper for the full
    rationale (duplicated rather than shared: these are two independent
    script entry points, the same convention ``seed_synthetic.py``/
    ``demo_query.py`` already establish for this package -- neither shares
    a base module with the other)."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m poseidon.scripts.cost_rollup",
        description="LLM cost roll-up per dimension, or a token-spike check, "
        "over llm_calls/turn_run.",
    )
    parser.add_argument(
        "--by",
        choices=_BY_CHOICES,
        default="day",
        help="grouping dimension for the roll-up (default: day); ignored with --spike-check",
    )
    parser.add_argument(
        "--since",
        default=None,
        type=_parse_since,
        metavar="<iso>",
        help="ISO-8601 lower bound on created_at (default: no lower bound)",
    )
    parser.add_argument(
        "--spike-check",
        action="store_true",
        help="list turns whose total tokens exceed Settings.token_spike_threshold",
    )
    return parser.parse_args(argv)


def _normalize_group(value: object) -> object:
    """``date``/``datetime`` group values (the "day" dimension) render as
    plain ISO strings for JSON; every other dimension is already a string."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _rollup(engine: Engine, *, by: str, since: datetime | None) -> list[dict]:
    group_expr = _GROUP_EXPR[by]
    since_clause = _ROLLUP_SINCE_CLAUSE if since is not None else ""
    sql = text(_ROLLUP_SQL.format(group_expr=group_expr, since_clause=since_clause))
    params = {"since": since} if since is not None else {}

    with rls_transaction(engine, _SCRIPT_USER_SUB, app_role=_ADMIN_ROLE) as conn:
        rows = conn.execute(sql, params).all()

    return [
        {
            "group": _normalize_group(row.grp),
            "calls": row.calls,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
        }
        for row in rows
    ]


def _spikes(engine: Engine, *, threshold: int, since: datetime | None) -> list[dict]:
    since_clause = _SPIKE_SINCE_CLAUSE if since is not None else ""
    sql = text(_SPIKE_SQL.format(since_clause=since_clause))
    params: dict[str, object] = {"threshold": threshold}
    if since is not None:
        params["since"] = since

    with rls_transaction(engine, _SCRIPT_USER_SUB, app_role=_ADMIN_ROLE) as conn:
        rows = conn.execute(sql, params).all()

    return [
        {
            "turn_id": str(row.id),
            "user_sub": row.user_sub,
            "kind": row.kind,
            "total_tokens": row.total_tokens,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
        }
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()

    if args.spike_check:
        threshold = settings.token_spike_threshold
        if threshold <= 0:
            print(json.dumps({"spike_check": "disabled", "threshold": threshold}))
            return 0

        engine = build_engine(settings.database_url)
        try:
            for spike in _spikes(engine, threshold=threshold, since=args.since):
                print(json.dumps(spike))
        finally:
            engine.dispose()
        return 0

    engine = build_engine(settings.database_url)
    try:
        for group in _rollup(engine, by=args.by, since=args.since):
            print(json.dumps(group))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

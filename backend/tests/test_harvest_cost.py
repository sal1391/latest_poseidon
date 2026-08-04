"""Tests for Phase 11 Task 4 (doc 06 observability): the harvest export
(``export_router_cases.py``) and cost roll-up + spike-check
(``cost_rollup.py``) -- the two OPERATOR scripts that close the
router-evidence loop this phase's earlier tasks opened (T1's admin read
role, T2's trace ids, T3's reconciliation).

Every row this suite creates is written THROUGH the real
:class:`~poseidon.core.runlog.RunLogWriter` (never a bare INSERT) -- the
same discipline ``test_runlog_rls.py`` already established for this phase.
Both scripts under test are exercised through their real, public
``main(argv)`` entry points -- never by importing a private helper and
calling it directly -- because that IS the interface `python -m poseidon.
scripts.<name>` invokes; a test that bypassed ``main`` could pass while the
actual CLI stayed broken.

**Why every dimension-grouping assertion is scoped by ``--since``.** Unlike
``test_runlog_rls.py``'s identity-scoped tests (RLS itself isolates one
test's rows from another's, so leftover rows from earlier runs are
"harmless... left as evidence" per that module's own docstring),
``export_router_cases``/``cost_rollup`` run under ``poseidon_admin``'s
``USING (true)`` policy BY DESIGN -- they see every row in the table, from
every user, with no RLS filter of their own to fall back on. Against this
long-lived, shared dev Postgres (other suites' rows are never deleted --
doc 05 section 7's "audit rows are never deleted" -- and this same
container also serves real localhost:5173 traffic), an unscoped roll-up
query would sum an unbounded, non-deterministic number of unrelated rows,
making "HAND-COMPUTED expected sums" impossible to pin. ``--since`` (read
from the database's own ``now()``, captured immediately before seeding --
never the test process's local clock, which could disagree with the
container's by however much host/container clock skew exists) is what
makes this suite's numbers exact rather than merely "at least". Every
seeded ``model_id``/``role``/``prompt_version`` value is additionally
suffixed with a fresh ``uuid4`` tag for the same reason, belt-and-suspenders
against a coincidental label collision with another suite's own fixture
values (``test_runlog_rls.py``'s own ``_full_turn`` helper hardcodes
``model_id="m"``, ``role="router"`` -- exactly the kind of value this task's
scripts would otherwise also aggregate).

**"Import the suite's loader" (the brief's own phrase).** ``routing_cases.
yml`` (the P5 router-decision suite, ``test_llm_loop.py``) has no dedicated
loader FUNCTION distinct from the test module itself -- it is one
``yaml.safe_load(ROUTING_CASES_PATH.read_text(encoding="utf-8"))`` line,
module-level, using the stdlib-adjacent ``yaml`` package as its only
parsing mechanism (confirmed by reading that file directly). "Importing the
suite's loader" therefore means importing THAT ``yaml`` reference from
``tests.test_llm_loop`` -- the identical object the suite itself calls
``.safe_load`` on -- rather than a second, independently-imported ``yaml``
that could in principle drift (a different installed version, a different
loader class) without this suite ever noticing. This codebase already
cross-imports between test modules routinely (``test_live_chat_sse.py``'s
``from tests.test_chat_orchestrator import REGISTRY, ...``, three more
examples the same way) -- this is that same convention, applied to a name
that happens to be a third-party module reference rather than a fixture or
constant. A plain ``import yaml`` is ALSO kept, deliberately, so both of
the brief's two bullets ("parses as YAML" / "matches the router-suite
loader") stay visibly, separately checked in the assertions below, even
though today they resolve to the same function object.

**Candidate schema is deliberately NOT the full suite schema.** The brief
pins the candidate format's own fields verbatim: ``question``/``expected:
TODO-human-review``, with the source turn id and trace id as YAML COMMENTS,
not data fields -- distinct from ``routing_cases.yml``'s own ``id``/
``user``/``expect``/``execution`` shape. A harvested candidate is
PRE-review evidence, not yet a runnable fixture; a human promotes it (see
``backend/tests/router_cases/candidates/README.md``) by hand-authoring the
real ``expect``/``execution`` blocks a raw harvested question can never
supply on its own (nobody has decided what the CORRECT routing decision
for a real, unreviewed question is yet). "Loads through the router-suite
loader" therefore proves YAML/byte-convention compatibility, not schema
identity -- there is no ``case["user"]``-shaped assertion against a
harvested candidate anywhere in this file, on purpose.
"""

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.chat.feedback import FeedbackStore
from poseidon.core.chat.history import HistoryStore
from poseidon.core.config import Settings, get_settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import build_engine, rls_transaction
from poseidon.core.runlog import RunLogWriter, redact_turns_for_conversation
from poseidon.core.util.uuid7 import uuid7
from poseidon.scripts import cost_rollup, export_router_cases, feedback_rollup
from tests.test_llm_loop import yaml as _router_suite_yaml

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0006)"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg harvest/cost-rollup/feedback-rollup tests need a "
        f"Postgres: {_UP_HINT}, {_MIGRATE_HINT}",
        allow_module_level=True,
    )

# Mirrors test_runlog_rls.py's own module-level probe exactly, extended
# (Phase 12 Task 3) to also require message_feedback (migration 0006) --
# mirrors test_feedback_store.py's own identical extension.
_DSN_ROLE_IS_SUPERUSER = False
try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'turn_run' AND column_name = 'redacted_at'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"turn_run.redacted_at does not exist - {_MIGRATE_HINT}",
                    allow_module_level=True,
                )
            _cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'message_feedback'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"message_feedback does not exist - {_MIGRATE_HINT}",
                    allow_module_level=True,
                )
            _cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            _DSN_ROLE_IS_SUPERUSER = _cur.fetchone()[0]
except psycopg.Error as exc:
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )

_APP_ROLE = "poseidon_app"
_EFFECTIVE_APP_ROLE = _APP_ROLE if _DSN_ROLE_IS_SUPERUSER else None

_CANDIDATES_DIR = Path(__file__).parent / "router_cases" / "candidates"

# The exact question text seeded for every exportable/error turn -- named
# once here so the seeding code and the assertions below can never drift
# apart on a hand-retyped literal. turn3 (redacted) and turn4
# (memory_update, question=None by construction) have no entry: neither is
# ever expected to survive into a candidate file.
_QUESTIONS = {
    "turn1": "What was GP for Maersk in April 2026?",
    "turn2": "Multi-call turn: router then a second call",
    "turn3": "This turn will be redacted",
    "turn5": "This turn errored",
    "turn6": "This turn spikes token usage",
}


@pytest.fixture
def pg_engine():
    """A fresh ``Engine`` per test -- see ``test_rls_policies.py``'s own
    identical fixture for the rationale (function-scoped)."""
    engine = build_engine(_DSN)
    try:
        yield engine
    finally:
        engine.dispose()


def _fresh_user_sub() -> str:
    return f"test|{uuid.uuid4().hex}"


def _writer(engine: Engine) -> RunLogWriter:
    return RunLogWriter(engine, app_role=_EFFECTIVE_APP_ROLE)


def _label(prefix: str, tag: str) -> str:
    return f"{prefix}-{tag}"


def _seed_turn(
    writer: RunLogWriter,
    *,
    user_sub: str,
    question: str | None,
    calls: list[dict],
    kind: str = "chat_turn",
    status: str = "ok",
    conversation_id: str | None = None,
    trace_id: str | None = None,
    error: dict | None = None,
) -> str:
    """One ``turn_run`` row plus ``len(calls)`` ``llm_calls`` rows, all
    through the real writer, finalized with the CALLER's own hand-summed
    totals -- mirrors ``RunLogWriter.finalize``'s own documented contract
    ("the caller already summed from its own appended records"), never
    re-derived by this helper. Returns the new ``turn_run.id``.

    ``client_turn_key``/``turn_index`` are ``None`` for
    ``kind="memory_update"`` (mirrors ``test_runlog_writer.py``'s own
    ``test_kind_memory_update_accepted_with_null_conversation_and_message``);
    ``message_id``/``answer_summary`` are ``None`` whenever the turn never
    produced a user-facing answer (``memory_update``, or a non-``ok``
    status) rather than always minting one.
    """
    is_memory_update = kind == "memory_update"
    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=conversation_id,
        client_turn_key=None if is_memory_update else str(uuid.uuid4()),
        turn_index=None if is_memory_update else 1,
        question=question,
        mode="default",
        parsed={},
        kind=kind,
        trace_id=trace_id,
    )
    assert handle is not None and handle.created is True, "writer.start_turn must succeed"
    for seq, call in enumerate(calls, start=1):
        writer.append_llm_call(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            seq=seq,
            provider="stub",
            model_id=call["model_id"],
            role=call["role"],
            prompt_version=call["prompt_version"],
            prompt_hash="h",
            input_tokens=call["input_tokens"],
            output_tokens=call["output_tokens"],
            latency_ms=10,
            status="ok",
        )
    produced_message = status == "ok" and not is_memory_update
    writer.finalize(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        status=status,
        message_id=str(uuid.uuid4()) if produced_message else None,
        answer_summary="done" if produced_message else None,
        input_tokens=sum(call["input_tokens"] for call in calls),
        output_tokens=sum(call["output_tokens"] for call in calls),
        latency_ms=50,
        error=error,
    )
    # str(...): against a real Postgres, `RETURNING id` hands back a genuine
    # uuid.UUID object for this native uuid column, not the plain str the
    # writer minted and inserted -- test_runlog_rls.py's own pg suite
    # documents this exact round-trip behavior (its "message_id::text"
    # comment). Normalized here so every caller of this helper can compare
    # the returned id against a plain string (a candidate filename's own
    # `.stem`, a spike-check line's own `turn_id`) unconditionally.
    return str(handle.turn_run_id)


@dataclass
class _Dataset:
    """One fresh, uniquely-tagged set of six ``turn_run`` rows exercising
    every kind/status/redaction combination this task's own brief names:
    ``turn1``/``turn2`` are ordinary exportable chat turns (``turn2`` is the
    multi-call one, proving per-call attribution), ``turn3`` is redacted
    (via the real T1 ``redact_turns_for_conversation`` path), ``turn4`` is
    ``kind="memory_update"``, ``turn5`` errored, and ``turn6`` alone carries
    enough tokens to cross any sane spike threshold this suite picks.

    See the module docstring's "Why every dimension-grouping assertion is
    scoped by --since" for why ``tag``/``since_iso`` exist at all.
    """

    since_iso: str
    tag: str
    turns: dict[str, str]
    users: dict[str, str]


def _seed_dataset(engine: Engine) -> _Dataset:
    tag = uuid.uuid4().hex[:8]
    writer = _writer(engine)
    users = {name: _fresh_user_sub() for name in ("user1", "user2", "user3", "user4")}
    conversation_to_redact = str(uuid.uuid4())

    with engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    turns = {
        "turn1": _seed_turn(
            writer,
            user_sub=users["user1"],
            question=_QUESTIONS["turn1"],
            trace_id=f"trace-{tag}",
            calls=[
                dict(
                    model_id=_label("model-a", tag),
                    role=_label("role-a", tag),
                    prompt_version=_label("pv-a", tag),
                    input_tokens=100,
                    output_tokens=50,
                )
            ],
        ),
        "turn2": _seed_turn(
            writer,
            user_sub=users["user2"],
            question=_QUESTIONS["turn2"],
            calls=[
                dict(
                    model_id=_label("model-b", tag),
                    role=_label("role-a", tag),
                    prompt_version=_label("pv-a", tag),
                    input_tokens=200,
                    output_tokens=80,
                ),
                dict(
                    model_id=_label("model-a", tag),
                    role=_label("role-b", tag),
                    prompt_version=_label("pv-b", tag),
                    input_tokens=30,
                    output_tokens=10,
                ),
            ],
        ),
        "turn3": _seed_turn(
            writer,
            user_sub=users["user1"],
            question=_QUESTIONS["turn3"],
            conversation_id=conversation_to_redact,
            calls=[
                dict(
                    model_id=_label("model-a", tag),
                    role=_label("role-a", tag),
                    prompt_version=_label("pv-a", tag),
                    input_tokens=40,
                    output_tokens=20,
                )
            ],
        ),
        "turn4": _seed_turn(
            writer,
            user_sub=users["user2"],
            question=None,
            kind="memory_update",
            calls=[
                dict(
                    model_id=_label("model-a", tag),
                    role=_label("role-a", tag),
                    prompt_version=_label("pv-a", tag),
                    input_tokens=15,
                    output_tokens=5,
                )
            ],
        ),
        "turn5": _seed_turn(
            writer,
            user_sub=users["user3"],
            question=_QUESTIONS["turn5"],
            status="error",
            error={"message": "boom"},
            calls=[
                dict(
                    model_id=_label("model-a", tag),
                    role=_label("role-a", tag),
                    prompt_version=_label("pv-a", tag),
                    input_tokens=60,
                    output_tokens=25,
                )
            ],
        ),
        "turn6": _seed_turn(
            writer,
            user_sub=users["user4"],
            question=_QUESTIONS["turn6"],
            calls=[
                dict(
                    model_id=_label("model-spike", tag),
                    role=_label("role-spike", tag),
                    prompt_version=_label("pv-spike", tag),
                    input_tokens=5000,
                    output_tokens=2000,
                )
            ],
        ),
    }

    with rls_transaction(engine, users["user1"], app_role=_EFFECTIVE_APP_ROLE) as conn:
        redacted_count = redact_turns_for_conversation(conn, conversation_to_redact)
    assert redacted_count == 1, "seeding bug: expected exactly turn3 to be redacted"

    return _Dataset(since_iso=since_iso, tag=tag, turns=turns, users=users)


@pytest.fixture
def dataset(pg_engine):
    ds = _seed_dataset(pg_engine)
    yield ds
    for turn_id in ds.turns.values():
        (_CANDIDATES_DIR / f"{turn_id}.yml").unlink(missing_ok=True)


def _expected_by_dimension(tag: str) -> dict[str, dict[str, tuple[int, int, int]]]:
    """Hand-computed ``(calls, input_tokens, output_tokens)`` per group, for
    the role/model/prompt_version dimensions -- added up BY HAND from
    ``_seed_dataset``'s own seven calls (turn1: 1, turn2: 2, turn3: 1,
    turn4: 1, turn5: 1, turn6: 1), independently of any SQL:

    role-a  = turn1 + turn2-call-a + turn3 + turn4 + turn5
            = (100+200+40+15+60, 50+80+20+5+25) = (415, 180), 5 calls
    role-b  = turn2-call-b = (30, 10), 1 call
    role-spike = turn6 = (5000, 2000), 1 call

    model-a = turn1 + turn2-call-b + turn3 + turn4 + turn5
            = (100+30+40+15+60, 50+10+20+5+25) = (245, 110), 5 calls
    model-b = turn2-call-a = (200, 80), 1 call
    model-spike = turn6 = (5000, 2000), 1 call

    prompt_version partitions identically to role in this fixture (pv-a
    carries the same five calls role-a does) -- a deliberate, harmless
    coincidence: the two dimensions are still independently proven correct
    because the assertions below key on the LABEL STRINGS ("pv-a-<tag>" vs
    "role-a-<tag>"), which a column mix-up bug would visibly fail on even
    though the two dimensions' call-sets happen to coincide.
    """
    return {
        "role": {
            _label("role-a", tag): (5, 415, 180),
            _label("role-b", tag): (1, 30, 10),
            _label("role-spike", tag): (1, 5000, 2000),
        },
        "model": {
            _label("model-a", tag): (5, 245, 110),
            _label("model-b", tag): (1, 200, 80),
            _label("model-spike", tag): (1, 5000, 2000),
        },
        "prompt_version": {
            _label("pv-a", tag): (5, 415, 180),
            _label("pv-b", tag): (1, 30, 10),
            _label("pv-spike", tag): (1, 5000, 2000),
        },
    }


def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors ``test_llm_loop.py``'s own ``_settings()`` helper: every
    Settings-derived env var cleared first, so an ambient shell value (this
    task's own manual doc-08 validation run sets ``DATABASE_URL``/
    ``S3_BUCKET`` in the SAME shell family) can never leak into a
    hermetic-by-intent test."""
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)


def _run_export(monkeypatch: pytest.MonkeyPatch, argv: list[str], *, database_url: str) -> int:
    monkeypatch.setenv("DATABASE_URL", database_url)
    return export_router_cases.main(argv)


def _run_cost_rollup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    argv: list[str],
    *,
    database_url: str,
    token_spike_threshold: int | None = None,
) -> tuple[int, list[dict]]:
    """Runs ``cost_rollup.main(argv)`` against a hermetic ``Settings`` (see
    ``_clear_settings_env``) and returns ``(exit_code, parsed_json_lines)``.
    ``get_settings`` is ``lru_cache``-wrapped (one process-wide singleton,
    by design, for the real long-lived server) -- cleared both before AND
    after so this test's own monkeypatched env never leaks a stale cached
    ``Settings`` into whichever test runs next in this same pytest process.
    """
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    if token_spike_threshold is not None:
        monkeypatch.setenv("TOKEN_SPIKE_THRESHOLD", str(token_spike_threshold))
    get_settings.cache_clear()
    try:
        exit_code = cost_rollup.main(argv)
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    return exit_code, lines


# ---------------------------------------------------------------------------
# export_router_cases.py
# ---------------------------------------------------------------------------


def test_export_default_excludes_memory_update_redacted_and_error_turns(dataset, monkeypatch):
    exit_code = _run_export(
        monkeypatch, ["--since", dataset.since_iso, "--limit", "50"], database_url=_DSN
    )
    assert exit_code == 0

    produced = {p.stem for p in _CANDIDATES_DIR.glob("*.yml")}
    mine = produced & set(dataset.turns.values())
    assert mine == {dataset.turns["turn1"], dataset.turns["turn2"], dataset.turns["turn6"]}


def test_export_include_errors_flips_error_inclusion(dataset, monkeypatch):
    exit_code = _run_export(
        monkeypatch,
        ["--since", dataset.since_iso, "--limit", "50", "--include-errors"],
        database_url=_DSN,
    )
    assert exit_code == 0

    produced = {p.stem for p in _CANDIDATES_DIR.glob("*.yml")}
    mine = produced & set(dataset.turns.values())
    assert mine == {
        dataset.turns["turn1"],
        dataset.turns["turn2"],
        dataset.turns["turn5"],
        dataset.turns["turn6"],
    }


def test_export_limit_caps_the_candidate_count(dataset, monkeypatch):
    exit_code = _run_export(
        monkeypatch, ["--since", dataset.since_iso, "--limit", "1"], database_url=_DSN
    )
    assert exit_code == 0

    produced = {p.stem for p in _CANDIDATES_DIR.glob("*.yml")}
    mine = produced & set(dataset.turns.values())
    assert len(mine) == 1


def test_export_output_is_yaml_and_matches_router_suite_loader(dataset, monkeypatch):
    exit_code = _run_export(
        monkeypatch, ["--since", dataset.since_iso, "--limit", "50"], database_url=_DSN
    )
    assert exit_code == 0

    turn1_id = dataset.turns["turn1"]
    turn6_id = dataset.turns["turn6"]
    turn1_text = (_CANDIDATES_DIR / f"{turn1_id}.yml").read_text(encoding="utf-8")
    turn6_text = (_CANDIDATES_DIR / f"{turn6_id}.yml").read_text(encoding="utf-8")

    assert not any(ord(ch) > 0x7F for ch in turn1_text), "candidate YAML must be pure ASCII on disk"

    # (1) "parses as YAML" -- generic.
    generic_loaded = yaml.safe_load(turn1_text)
    # (2) "matches the router-suite loader" -- imported, not reimplemented;
    # see the module docstring's "Import the suite's loader" section.
    suite_loaded = _router_suite_yaml.safe_load(turn1_text)
    expected_turn1 = {"question": _QUESTIONS["turn1"], "expected": "TODO-human-review"}
    assert generic_loaded == expected_turn1
    assert suite_loaded == expected_turn1

    # Byte-convention check, deliberately separate from the dict-equality
    # checks above: yaml.safe_load parses `question: "..."` and
    # `"question": "..."` (a quoted KEY) identically, so equality alone
    # cannot tell a bare key (routing_cases.yml's own `user: "..."`/`id:
    # foo` convention) apart from a quoted one. This exact line format is
    # what this task's own manual doc-08 validation run caught PyYAML
    # getting wrong on the first implementation attempt (`default_style`
    # forced quoting onto the mapping key too, not just the value) --
    # pinned here now so a regression fails a test, not only an eyeball
    # check of a real exported file.
    assert f'question: "{_QUESTIONS["turn1"]}"' in turn1_text
    assert '"question"' not in turn1_text

    # turn1 was seeded with an explicit trace_id; turn6 was not -- both
    # branches of the comment-rendering logic get proven, not just one.
    assert f"turn_id: {turn1_id}" in turn1_text
    assert f"trace_id: trace-{dataset.tag}" in turn1_text

    turn6_loaded = _router_suite_yaml.safe_load(turn6_text)
    assert turn6_loaded == {"question": _QUESTIONS["turn6"], "expected": "TODO-human-review"}
    assert f"turn_id: {turn6_id}" in turn6_text
    assert "trace_id: (none)" in turn6_text


# ---------------------------------------------------------------------------
# cost_rollup.py -- per-dimension roll-up, hand-computed sums
# ---------------------------------------------------------------------------


def test_cost_rollup_hand_computed_sums_per_dimension_with_per_call_attribution(
    pg_engine, monkeypatch, capsys
):
    """Proves per-call attribution directly: turn2 is ONE turn with TWO
    calls carrying different role/model/prompt_version, and each call lands
    in its OWN group's sum below, never merged into a single per-turn
    bucket (turn_run itself has no role/model/prompt_version column at all
    -- there is no OTHER way to answer "--by role" except grouping
    llm_calls rows directly, which is exactly what this proves happened)."""
    ds = _seed_dataset(pg_engine)
    expected = _expected_by_dimension(ds.tag)

    for by, expected_groups in expected.items():
        exit_code, lines = _run_cost_rollup(
            monkeypatch, capsys, ["--by", by, "--since", ds.since_iso], database_url=_DSN
        )
        assert exit_code == 0
        by_group = {
            line["group"]: (line["calls"], line["input_tokens"], line["output_tokens"])
            for line in lines
        }
        for group_name, expected_tuple in expected_groups.items():
            assert by_group.get(group_name) == expected_tuple, (by, group_name, by_group)


def test_cost_rollup_by_user_hand_computed_sums(pg_engine, monkeypatch, capsys):
    ds = _seed_dataset(pg_engine)
    expected = {
        ds.users["user1"]: (2, 140, 70),  # turn1 (100/50) + turn3 (40/20)
        ds.users["user2"]: (3, 245, 95),  # turn2 both calls (230/90) + turn4 (15/5)
        ds.users["user3"]: (1, 60, 25),  # turn5
        ds.users["user4"]: (1, 5000, 2000),  # turn6
    }
    exit_code, lines = _run_cost_rollup(
        monkeypatch, capsys, ["--by", "user", "--since", ds.since_iso], database_url=_DSN
    )
    assert exit_code == 0
    by_group = {
        line["group"]: (line["calls"], line["input_tokens"], line["output_tokens"])
        for line in lines
    }
    for user_sub, expected_tuple in expected.items():
        assert by_group.get(user_sub) == expected_tuple


def test_cost_rollup_by_day_groups_by_utc_calendar_date(pg_engine, monkeypatch, capsys):
    ds = _seed_dataset(pg_engine)
    with pg_engine.connect() as conn:
        today = conn.execute(text("SELECT (now() AT TIME ZONE 'UTC')::date")).scalar_one()
    exit_code, lines = _run_cost_rollup(
        monkeypatch, capsys, ["--by", "day", "--since", ds.since_iso], database_url=_DSN
    )
    assert exit_code == 0
    by_group = {
        line["group"]: (line["calls"], line["input_tokens"], line["output_tokens"])
        for line in lines
    }
    assert by_group.get(today.isoformat()) == (7, 5445, 2190)


def test_cost_rollup_includes_memory_update_and_redacted_turns_as_real_spend(
    pg_engine, monkeypatch, capsys
):
    """Isolates the brief's own explicit rule with a minimal, dedicated
    two-turn dataset rather than relying only on the bigger cross-dimension
    test above to imply it: llm_calls survives redaction untouched (doc 05
    section 7 names it "untouched by design"), and a memory_update turn's
    calls are real spend -- cost_rollup excludes NEITHER, the opposite of
    export_router_cases' rule for the identical two turns."""
    tag = uuid.uuid4().hex[:8]
    writer = _writer(pg_engine)
    user = _fresh_user_sub()
    conversation_id = str(uuid.uuid4())
    role = _label("solo-role", tag)

    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    _seed_turn(
        writer,
        user_sub=user,
        question="will be redacted",
        conversation_id=conversation_id,
        calls=[dict(model_id="m", role=role, prompt_version="v1", input_tokens=7, output_tokens=3)],
    )
    _seed_turn(
        writer,
        user_sub=user,
        question=None,
        kind="memory_update",
        calls=[
            dict(model_id="m", role=role, prompt_version="v1", input_tokens=11, output_tokens=4)
        ],
    )
    with rls_transaction(pg_engine, user, app_role=_EFFECTIVE_APP_ROLE) as conn:
        assert redact_turns_for_conversation(conn, conversation_id) == 1

    exit_code, lines = _run_cost_rollup(
        monkeypatch, capsys, ["--by", "role", "--since", since_iso], database_url=_DSN
    )
    assert exit_code == 0
    by_group = {
        line["group"]: (line["calls"], line["input_tokens"], line["output_tokens"])
        for line in lines
    }
    assert by_group.get(role) == (2, 18, 7)  # 7+11=18, 3+4=7 -- both calls counted


# ---------------------------------------------------------------------------
# cost_rollup.py --spike-check
# ---------------------------------------------------------------------------


def test_spike_check_flags_exactly_the_seeded_over_threshold_turn(pg_engine, monkeypatch, capsys):
    ds = _seed_dataset(pg_engine)
    exit_code, lines = _run_cost_rollup(
        monkeypatch,
        capsys,
        ["--spike-check", "--since", ds.since_iso],
        database_url=_DSN,
        token_spike_threshold=1000,
    )
    assert exit_code == 0

    flagged = {line["turn_id"] for line in lines}
    mine = flagged & set(ds.turns.values())
    assert mine == {ds.turns["turn6"]}
    turn6_line = next(line for line in lines if line["turn_id"] == ds.turns["turn6"])
    assert turn6_line["total_tokens"] == 7000


def test_spike_check_flags_memory_update_and_redacted_turns_too(pg_engine, monkeypatch, capsys):
    """Fix round 1 (review finding, Important): JC-4 (task-4-report.md)
    claims ``--spike-check`` has no ``kind``/``redacted_at`` filter -- the
    SQL itself already has none, but nothing proved it before this test.
    Two turns, both over threshold: one ``kind='memory_update'`` (the same
    "real spend" reasoning the cost roll-up applies), and one that has
    since been REDACTED via the real T1 ``redact_turns_for_conversation``
    path. ``turn_run.input_tokens``/``output_tokens`` are never touched by
    redaction (``runlog.py``'s own docstring: "untouched by design"), so
    this test doubles as proof that redacting a conversation never quietly
    disables spike detection on the spend it already recorded.
    """
    tag = uuid.uuid4().hex[:8]
    writer = _writer(pg_engine)
    user = _fresh_user_sub()
    conversation_id = str(uuid.uuid4())

    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    memory_update_turn = _seed_turn(
        writer,
        user_sub=user,
        question=None,
        kind="memory_update",
        calls=[
            dict(
                model_id=_label("model-mu", tag),
                role=_label("role-mu", tag),
                prompt_version="v1",
                input_tokens=6000,
                output_tokens=3000,
            )
        ],
    )
    redacted_turn = _seed_turn(
        writer,
        user_sub=user,
        question="will be redacted, still spikes",
        conversation_id=conversation_id,
        calls=[
            dict(
                model_id=_label("model-rd", tag),
                role=_label("role-rd", tag),
                prompt_version="v1",
                input_tokens=7000,
                output_tokens=4000,
            )
        ],
    )
    with rls_transaction(pg_engine, user, app_role=_EFFECTIVE_APP_ROLE) as conn:
        assert redact_turns_for_conversation(conn, conversation_id) == 1

    exit_code, lines = _run_cost_rollup(
        monkeypatch,
        capsys,
        ["--spike-check", "--since", since_iso],
        database_url=_DSN,
        token_spike_threshold=1000,
    )
    assert exit_code == 0

    flagged = {line["turn_id"]: line for line in lines}

    assert memory_update_turn in flagged, "over-threshold memory_update turn must be flagged"
    assert flagged[memory_update_turn]["total_tokens"] == 9000
    assert flagged[memory_update_turn]["kind"] == "memory_update"

    assert redacted_turn in flagged, "over-threshold REDACTED turn must still be flagged"
    assert flagged[redacted_turn]["total_tokens"] == 11000


def test_spike_check_threshold_zero_is_disabled_and_never_touches_the_database(monkeypatch, capsys):
    """threshold=0 must short-circuit BEFORE any database connection is
    attempted -- proven, not just asserted, by pointing DATABASE_URL at a
    host nothing listens on and confirming this still exits 0 instead of a
    connection error."""
    unreachable_dsn = "postgresql+psycopg://nope:nope@127.0.0.1:1/nope"
    exit_code, lines = _run_cost_rollup(
        monkeypatch,
        capsys,
        ["--spike-check"],
        database_url=unreachable_dsn,
        token_spike_threshold=0,
    )
    assert exit_code == 0
    assert lines == [{"spike_check": "disabled", "threshold": 0}]


# ---------------------------------------------------------------------------
# Phase 12 Task 3: thumbs-down-first harvest + verdict roll-up
#
# **Skill attribution, disclosed (per this task's own brief requirement):**
# a real turn's ROUTED skill is recorded on ``tool_calls.tool`` -- literally
# ``ToolRecord.skill_id`` (``core/llm/loop.py``), written by ``orchestrator.
# py``'s ``_append_records`` as ``tool=record.skill_id`` -- never on
# ``turn_run.parsed``, which carries only ``_parsed_to_loggable_dict``'s
# slot/entity state (mode, customer, periods -- see that function's own
# docstring) and has no skill-id field at all. Confirmed by reading both
# modules directly, not assumed; ``test_feedback_rollup_by_skill_reads_
# tool_calls_tool_not_turn_run_parsed`` below pins this with a DECOY value
# in ``parsed`` that must NEVER surface as a rollup group.
#
# Every seeded turn here goes through the SAME real write path Task 1/2
# ship: ``HistoryStore.write_assistant_message`` for the ``messages`` row,
# ``RunLogWriter`` for ``turn_run``/``llm_calls``/``tool_calls``, and
# ``FeedbackStore.upsert`` for ``message_feedback`` -- never a bare INSERT
# for any of the four (this suite's own established discipline, see the
# module docstring). ``_seed_feedback_turn`` below is the one helper that
# does all four, mirroring ``test_feedback_store.py``'s own ``_full_message_
# with_run`` extended with the run-log children this task's scripts read.
# ---------------------------------------------------------------------------

_SENTINEL_PROMPT_HASH = "SENTINEL-PROMPT-HASH-MUST-NEVER-APPEAR-IN-CANDIDATE-YAML"
_SENTINEL_ARG_KEY = "sentinel_arg_key"
_SENTINEL_ARG_VALUE = "SENTINEL-ARG-VALUE-MUST-NEVER-APPEAR-IN-CANDIDATE-YAML"


def _seed_feedback_turn(
    engine: Engine,
    *,
    user_sub: str,
    question: str,
    skill_id: str | None,
    role: str | None,
    prompt_version: str | None = None,
    verdict: str | None,
    comment: str | None = None,
    answer_summary: str = "the answer",
    kind: str = "chat_turn",
) -> tuple[str, str]:
    """One full, real-path turn: a fresh conversation, a linked ``messages``
    row, a ``turn_run`` row at the SAME id, one ``llm_calls`` row (when
    ``role`` is given, carrying the SENTINEL ``prompt_hash`` above) and one
    ``tool_calls`` row (when ``skill_id`` is given, carrying the SENTINEL
    ``args`` above) -- the two sentinels exist so a candidate export can be
    asserted to NEVER contain either (the P11 exclusion rule, re-asserted on
    this new feedback path) -- and, when ``verdict`` is given, a real
    ``message_feedback`` row written through :class:`FeedbackStore`. Returns
    ``(turn_run_id, message_id)``.
    """
    history = HistoryStore(engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    conversation, _opener = history.create_conversation()
    writer = _writer(engine)
    turn_id = str(uuid7())
    message_id = str(uuid7())

    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=conversation["id"],
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question=question,
        mode="default",
        parsed={"subject_text": question},
        kind=kind,
        turn_run_id=turn_id,
    )
    assert handle is not None and handle.created is True, "writer.start_turn must succeed"

    if role is not None:
        writer.append_llm_call(
            turn_run_id=turn_id,
            user_sub=user_sub,
            seq=1,
            provider="stub",
            model_id="model-" + role,
            role=role,
            prompt_version=prompt_version,
            prompt_hash=_SENTINEL_PROMPT_HASH,
            input_tokens=11,
            output_tokens=7,
            latency_ms=5,
            status="ok",
        )
    if skill_id is not None:
        writer.append_tool_call(
            turn_run_id=turn_id,
            user_sub=user_sub,
            seq=1,
            tool=skill_id,
            server=None,
            args={_SENTINEL_ARG_KEY: _SENTINEL_ARG_VALUE},
            result_digest=None,
            status="ok",
            latency_ms=5,
        )

    history.write_assistant_message(
        conversation["id"],
        {
            "id": message_id,
            "role": "assistant",
            "parts": [{"kind": "text", "payload": {"markdown": answer_summary}}],
        },
        turn_id,
    )
    writer.finalize(
        turn_run_id=turn_id,
        user_sub=user_sub,
        status="ok",
        message_id=message_id,
        answer_summary=answer_summary,
        input_tokens=11,
        output_tokens=7,
        latency_ms=20,
    )

    if verdict is not None:
        FeedbackStore(engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub).upsert(
            message_id, verdict, comment
        )

    return turn_id, message_id


def _conversation_id_of(engine: Engine, turn_run_id: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT conversation_id FROM turn_run WHERE id = :id"), {"id": turn_run_id}
            ).scalar_one()
        )


def test_export_thumbs_down_candidates_are_selected_before_others_under_a_tight_limit(
    pg_engine, monkeypatch
):
    """Priority proof (doc 06 section 7 / D25): under a ``--limit`` tighter
    than the total eligible pool, thumbs-down turns fill the budget FIRST,
    regardless of how they interleave in time with non-down turns. ``down2``
    is seeded AFTER ``up_old`` on purpose -- a recency-only ordering would
    pick ``up_old`` over ``down2``; this proves priority wins instead. Three
    sequential export runs at increasing ``--limit`` also prove the
    remaining budget still falls back to the existing oldest-first order
    once every down candidate is placed.
    """
    user = _fresh_user_sub()
    tag = uuid.uuid4().hex[:8]
    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    down1_id, _ = _seed_feedback_turn(
        pg_engine,
        user_sub=user,
        question=_label("q-down1", tag),
        skill_id=_label("skill-a", tag),
        role=_label("role-a", tag),
        prompt_version="v1",
        verdict="down",
        comment="bad",
    )
    up_old_id, _ = _seed_feedback_turn(
        pg_engine,
        user_sub=user,
        question=_label("q-up-old", tag),
        skill_id=_label("skill-a", tag),
        role=_label("role-a", tag),
        prompt_version="v1",
        verdict="up",
    )
    down2_id, _ = _seed_feedback_turn(
        pg_engine,
        user_sub=user,
        question=_label("q-down2", tag),
        skill_id=_label("skill-b", tag),
        role=_label("role-b", tag),
        prompt_version="v1",
        verdict="down",
        comment="also bad",
    )
    plain_new_id, _ = _seed_feedback_turn(
        pg_engine,
        user_sub=user,
        question=_label("q-plain-new", tag),
        skill_id=_label("skill-b", tag),
        role=_label("role-b", tag),
        prompt_version="v1",
        verdict=None,
    )
    all_ids = {down1_id, up_old_id, down2_id, plain_new_id}

    def _produced_of_interest() -> set[str]:
        return {p.stem for p in _CANDIDATES_DIR.glob("*.yml")} & all_ids

    try:
        exit_code = _run_export(
            monkeypatch, ["--since", since_iso, "--limit", "2"], database_url=_DSN
        )
        assert exit_code == 0
        assert _produced_of_interest() == {down1_id, down2_id}, (
            "both down-verdict turns must fill a tight 2-slot budget before "
            "either non-down turn, even though up_old is chronologically "
            "older than down2"
        )

        exit_code = _run_export(
            monkeypatch, ["--since", since_iso, "--limit", "3"], database_url=_DSN
        )
        assert exit_code == 0
        assert _produced_of_interest() == {down1_id, down2_id, up_old_id}, (
            "the third slot goes to the oldest remaining non-down turn"
        )

        exit_code = _run_export(
            monkeypatch, ["--since", since_iso, "--limit", "10"], database_url=_DSN
        )
        assert exit_code == 0
        assert _produced_of_interest() == all_ids
    finally:
        for turn_id in all_ids:
            (_CANDIDATES_DIR / f"{turn_id}.yml").unlink(missing_ok=True)


def test_export_thumbs_down_candidate_carries_feedback_block_and_reasserts_exclusions(
    pg_engine, monkeypatch
):
    """The extended candidate shape (doc 06 section 7 / D25): schema stays
    ``question``/``expected: TODO-human-review`` (the eb67500 ruling),
    extended with exactly one new top-level ``feedback:`` block carrying the
    verdict, comment, and full run context (answer_summary, parsed, and the
    turn's tool/LLM calls by name/role + status only) -- and the source
    message id as a YAML COMMENT, matching turn_id/trace_id's own existing
    comment-not-data convention. Args and prompt_hash (the P11 exclusion
    rule) must never reach the file, proven here by asserting the sentinel
    values seeded specifically to catch a regression are absent.
    """
    user = _fresh_user_sub()
    tag = uuid.uuid4().hex[:8]
    skill_id = _label("skill-content", tag)
    role = _label("role-content", tag)
    question = _label("content-question", tag)
    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    turn_id, message_id = _seed_feedback_turn(
        pg_engine,
        user_sub=user,
        question=question,
        skill_id=skill_id,
        role=role,
        prompt_version="pv-content",
        verdict="down",
        comment="the port name is wrong",
        answer_summary="Answer: some summary text",
    )

    try:
        exit_code = _run_export(
            monkeypatch, ["--since", since_iso, "--limit", "50"], database_url=_DSN
        )
        assert exit_code == 0

        candidate_text = (_CANDIDATES_DIR / f"{turn_id}.yml").read_text(encoding="utf-8")
        assert not any(ord(ch) > 0x7F for ch in candidate_text), (
            "candidate YAML must be pure ASCII on disk"
        )

        # P11 exclusion rule, re-asserted on the feedback path.
        assert _SENTINEL_ARG_VALUE not in candidate_text
        assert _SENTINEL_ARG_KEY not in candidate_text
        assert _SENTINEL_PROMPT_HASH not in candidate_text

        assert f"# source message_id: {message_id}" in candidate_text

        loaded = _router_suite_yaml.safe_load(candidate_text)
        assert loaded == {
            "question": question,
            "expected": "TODO-human-review",
            "feedback": {
                "verdict": "down",
                "comment": "the port name is wrong",
                "answer_summary": "Answer: some summary text",
                "parsed": {"subject_text": question},
                "tool_calls": [{"tool": skill_id, "status": "ok"}],
                "llm_calls": [{"role": role, "status": "ok"}],
            },
        }
    finally:
        (_CANDIDATES_DIR / f"{turn_id}.yml").unlink(missing_ok=True)


def test_export_thumbs_down_still_excludes_redacted_turns(pg_engine, monkeypatch):
    """Re-asserts the redaction exclusion on the NEW feedback path: a
    thumbs-down verdict does not exempt its turn from doc 05 section 7's
    deletion contract -- once redacted, the turn must never be exported,
    priority candidate or not. (``kind='memory_update'`` is not separately
    re-tested here: that combination cannot be constructed through any real
    write path at all -- a memory_update turn never produces a message
    (``_seed_turn``'s own ``produced_message`` gate; ``FeedbackStore.
    upsert`` requires a real, visible message), so there is no route by
    which one could ever receive feedback in the first place. The base
    ``kind = 'chat_turn'`` filter stays on the down-priority query
    regardless -- see the implementation -- but exercising it here would
    need a hand-crafted, unreachable-in-practice row.)
    """
    user = _fresh_user_sub()
    tag = uuid.uuid4().hex[:8]
    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    turn_id, _message_id = _seed_feedback_turn(
        pg_engine,
        user_sub=user,
        question=_label("will-be-redacted", tag),
        skill_id=_label("skill-r", tag),
        role=_label("role-r", tag),
        prompt_version="v1",
        verdict="down",
        comment="wrong",
    )
    conversation_id = _conversation_id_of(pg_engine, turn_id)
    with rls_transaction(pg_engine, user, app_role=_EFFECTIVE_APP_ROLE) as conn:
        assert redact_turns_for_conversation(conn, conversation_id) == 1

    try:
        exit_code = _run_export(
            monkeypatch, ["--since", since_iso, "--limit", "50"], database_url=_DSN
        )
        assert exit_code == 0
        produced = {p.stem for p in _CANDIDATES_DIR.glob("*.yml")}
        assert turn_id not in produced, "a redacted turn must never be exported, down-voted or not"
    finally:
        (_CANDIDATES_DIR / f"{turn_id}.yml").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# feedback_rollup.py -- verdict rates by skill/role/prompt_version
# ---------------------------------------------------------------------------


def _run_feedback_rollup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    argv: list[str],
    *,
    database_url: str,
) -> tuple[int, list[dict]]:
    """Runs ``feedback_rollup.main(argv)`` through its real, public entry
    point -- same discipline as ``_run_export``/``_run_cost_rollup`` above.
    No ``Settings`` hermeticity dance needed here (unlike ``_run_cost_
    rollup``): this script reads ``DATABASE_URL`` straight from the process
    environment, like ``export_router_cases.py`` does, and never touches
    ``Settings`` at all -- see the implementation's own module docstring for
    why."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    exit_code = feedback_rollup.main(argv)
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    return exit_code, lines


def _seed_feedback_rollup_dataset(engine: Engine) -> tuple[str, str]:
    """Seeds the 7-turn fixture ``_expected_feedback_by_dimension`` is hand-
    computed from. Returns ``(tag, since_iso)``."""
    tag = uuid.uuid4().hex[:8]
    user = _fresh_user_sub()
    with engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    # (skill, role, prompt_version, verdict) -- see
    # _expected_feedback_by_dimension's own docstring for the hand-summed
    # per-group counts these seven turns are added up from.
    specs = [
        ("a", "x", "p1", "down"),
        ("a", "x", "p1", "down"),
        ("a", "y", "p2", "up"),
        ("b", "y", "p2", "down"),
        ("b", "z", "p3", "down"),
        ("b", "z", "p3", "down"),
        ("b", "z", "p3", "up"),
    ]
    for i, (skill, role, pv, verdict) in enumerate(specs):
        _seed_feedback_turn(
            engine,
            user_sub=user,
            question=f"rollup-q-{tag}-{i}",
            skill_id=_label(f"skill-{skill}", tag),
            role=_label(f"role-{role}", tag),
            prompt_version=_label(f"pv-{pv}", tag),
            verdict=verdict,
            comment="bad" if verdict == "down" else None,
        )
    return tag, since_iso


def _expected_feedback_by_dimension(tag: str) -> dict[str, dict[str, tuple[int, int, float]]]:
    """Hand-computed ``(up, down, down_rate)`` per group, added up BY HAND
    from ``_seed_feedback_rollup_dataset``'s own seven turns (numbered 1-7
    in seeding order), independently of any SQL:

    skill-a = turns 1,2 (down) + 3 (up)          -> up=1 down=2 rate=2/3=0.6667
    skill-b = turns 4,5,6 (down) + 7 (up)        -> up=1 down=3 rate=3/4=0.75

    role-x  = turns 1,2 (both down)              -> up=0 down=2 rate=1.0
    role-y  = turns 3 (up) + 4 (down)            -> up=1 down=1 rate=0.5
    role-z  = turns 5,6 (down) + 7 (up)          -> up=1 down=2 rate=2/3=0.6667

    prompt_version partitions identically to role in this fixture (pv-p1/p2/
    p3 each carry the exact same turns role-x/y/z do) -- a deliberate,
    harmless coincidence, exactly like cost_rollup's own test dataset (see
    ``_expected_by_dimension``'s identical disclosure above): still
    independently proven correct because the assertions key on the LABEL
    STRINGS, which a column mix-up would visibly fail on regardless.
    """
    return {
        "skill": {
            _label("skill-a", tag): (1, 2, 0.6667),
            _label("skill-b", tag): (1, 3, 0.75),
        },
        "role": {
            _label("role-x", tag): (0, 2, 1.0),
            _label("role-y", tag): (1, 1, 0.5),
            _label("role-z", tag): (1, 2, 0.6667),
        },
        "prompt_version": {
            _label("pv-p1", tag): (0, 2, 1.0),
            _label("pv-p2", tag): (1, 1, 0.5),
            _label("pv-p3", tag): (1, 2, 0.6667),
        },
    }


def test_feedback_rollup_hand_computed_rates_per_dimension(pg_engine, monkeypatch, capsys):
    tag, since_iso = _seed_feedback_rollup_dataset(pg_engine)
    expected = _expected_feedback_by_dimension(tag)

    for by, expected_groups in expected.items():
        exit_code, lines = _run_feedback_rollup(
            monkeypatch, capsys, ["--by", by, "--since", since_iso], database_url=_DSN
        )
        assert exit_code == 0
        by_group = {line["group"]: (line["up"], line["down"], line["down_rate"]) for line in lines}
        for group_name, expected_tuple in expected_groups.items():
            assert by_group.get(group_name) == expected_tuple, (by, group_name, by_group)


def test_feedback_rollup_by_skill_reads_tool_calls_tool_not_turn_run_parsed(
    pg_engine, monkeypatch, capsys
):
    """Skill-attribution disclosure, pinned (this task's own required
    check): a real turn's routed skill lives on ``tool_calls.tool``, never
    on ``turn_run.parsed`` -- see the section banner above for the full
    reading. This turn's own ``parsed`` JSON carries a DECOY value that
    merely looks like a skill id; if ``feedback_rollup.py`` ever read
    ``parsed`` instead of ``tool_calls.tool``, this test would see the decoy
    string as a rollup group and fail.
    """
    tag = uuid.uuid4().hex[:8]
    user = _fresh_user_sub()
    real_skill = _label("real-skill", tag)
    decoy_skill = _label("decoy-parsed-skill", tag)
    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    history = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user)
    conversation, _opener = history.create_conversation()
    writer = _writer(pg_engine)
    turn_id = str(uuid7())
    message_id = str(uuid7())
    handle = writer.start_turn(
        user_sub=user,
        conversation_id=conversation["id"],
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="decoy test",
        mode="default",
        # The decoy: a plausible-looking key that is NOT what
        # feedback_rollup.py is supposed to read.
        parsed={"skill_id": decoy_skill},
        turn_run_id=turn_id,
    )
    assert handle is not None and handle.created is True
    writer.append_tool_call(
        turn_run_id=turn_id,
        user_sub=user,
        seq=1,
        tool=real_skill,
        server=None,
        args={},
        result_digest=None,
        status="ok",
        latency_ms=1,
    )
    history.write_assistant_message(
        conversation["id"],
        {
            "id": message_id,
            "role": "assistant",
            "parts": [{"kind": "text", "payload": {"markdown": "a"}}],
        },
        turn_id,
    )
    writer.finalize(
        turn_run_id=turn_id,
        user_sub=user,
        status="ok",
        message_id=message_id,
        answer_summary="a",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )
    FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user).upsert(
        message_id, "down", "bad"
    )

    exit_code, lines = _run_feedback_rollup(
        monkeypatch, capsys, ["--by", "skill", "--since", since_iso], database_url=_DSN
    )
    assert exit_code == 0
    groups = {line["group"] for line in lines}
    assert real_skill in groups
    assert decoy_skill not in groups


def test_feedback_rollup_does_not_double_count_a_turn_with_multiple_calls_of_the_same_role(
    pg_engine, monkeypatch, capsys
):
    """A real routed turn can issue MORE THAN ONE ``llm_calls`` row (the
    agent loop iterates: route -> tool -> route again -> end_turn) --
    confirmed directly against this environment's own compose Postgres
    while validating this task (two ``role='router'`` rows recorded for one
    genuine turn). A naive join straight against ``llm_calls`` would count
    that ONE verdict twice for the same group; ``COUNT(DISTINCT message_
    feedback.id)`` is what keeps it at one."""
    tag = uuid.uuid4().hex[:8]
    user = _fresh_user_sub()
    role = _label("multi-call-role", tag)
    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    history = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user)
    conversation, _opener = history.create_conversation()
    writer = _writer(pg_engine)
    turn_id = str(uuid7())
    message_id = str(uuid7())
    handle = writer.start_turn(
        user_sub=user,
        conversation_id=conversation["id"],
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="multi-call",
        mode="default",
        parsed={},
        turn_run_id=turn_id,
    )
    assert handle is not None and handle.created is True
    for seq in (1, 2):
        writer.append_llm_call(
            turn_run_id=turn_id,
            user_sub=user,
            seq=seq,
            provider="stub",
            model_id="m",
            role=role,
            prompt_version="v1",
            prompt_hash="h",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            status="ok",
        )
    history.write_assistant_message(
        conversation["id"],
        {
            "id": message_id,
            "role": "assistant",
            "parts": [{"kind": "text", "payload": {"markdown": "a"}}],
        },
        turn_id,
    )
    writer.finalize(
        turn_run_id=turn_id,
        user_sub=user,
        status="ok",
        message_id=message_id,
        answer_summary="a",
        input_tokens=2,
        output_tokens=2,
        latency_ms=2,
    )
    FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user).upsert(
        message_id, "down", "x"
    )

    exit_code, lines = _run_feedback_rollup(
        monkeypatch, capsys, ["--by", "role", "--since", since_iso], database_url=_DSN
    )
    assert exit_code == 0
    by_group = {line["group"]: (line["up"], line["down"]) for line in lines}
    assert by_group.get(role) == (0, 1), "one verdict must count once, not once per llm_calls row"


def test_feedback_rollup_includes_feedback_from_redacted_turns(pg_engine, monkeypatch, capsys):
    """The opposite rule from ``export_router_cases.py``'s own thumbs-down
    path (which excludes redacted turns entirely, re-asserted above):
    redaction erases CONTENT (doc 05 section 7), not the fact that a verdict
    was cast. ``tool_calls.tool``/``status`` and ``llm_calls.role``/
    ``prompt_version``/``status`` all survive redaction untouched (only
    ``args``/``result_digest`` on ``tool_calls`` and ``question``/``answer_
    summary``/``parsed`` on ``turn_run`` are nulled -- ``runlog.py``'s own
    ``_REDACT_TOOL_CALLS_SQL``/``_REDACT_TURN_RUN_SQL``), so a redacted
    turn's verdict still counts here -- mirrors ``cost_rollup.py``'s own
    identical stance for token spend."""
    tag = uuid.uuid4().hex[:8]
    user = _fresh_user_sub()
    skill = _label("redacted-skill", tag)
    with pg_engine.connect() as conn:
        since_iso = conn.execute(text("SELECT now()")).scalar_one().isoformat()

    turn_id, _message_id = _seed_feedback_turn(
        pg_engine,
        user_sub=user,
        question="will be redacted",
        skill_id=skill,
        role=_label("redacted-role", tag),
        prompt_version="v1",
        verdict="down",
        comment="wrong",
    )
    conversation_id = _conversation_id_of(pg_engine, turn_id)
    with rls_transaction(pg_engine, user, app_role=_EFFECTIVE_APP_ROLE) as conn:
        assert redact_turns_for_conversation(conn, conversation_id) == 1

    exit_code, lines = _run_feedback_rollup(
        monkeypatch, capsys, ["--by", "skill", "--since", since_iso], database_url=_DSN
    )
    assert exit_code == 0
    by_group = {line["group"]: (line["up"], line["down"]) for line in lines}
    assert by_group.get(skill) == (0, 1)


def test_feedback_rollup_module_is_ascii_on_disk():
    """Matches this file's own ``test_harvest_cost_module_is_ascii_on_disk``
    convention, extended to the new sibling module (``test_feedback_store.
    py``'s own ``test_feedback_module_and_this_test_module_are_ascii_on_
    disk`` is the precedent for checking a PRODUCTION module's bytes from a
    test file, not only the test file's own)."""
    offending = sorted(
        {byte for byte in Path(feedback_rollup.__file__).read_bytes() if byte > 0x7F}
    )
    assert not offending, "feedback_rollup.py holds non-ASCII bytes: " + repr(offending)


def test_harvest_cost_module_is_ascii_on_disk():
    """Matches the codebase-wide ASCII-on-disk convention (e.g.
    ``test_runlog_rls_module_is_ascii_on_disk``)."""
    offending = sorted({byte for byte in Path(__file__).read_bytes() if byte > 0x7F})
    assert not offending, f"{Path(__file__).name} holds non-ASCII bytes: {offending}"

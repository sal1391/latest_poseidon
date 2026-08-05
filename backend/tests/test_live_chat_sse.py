"""Tests for Phase 6 Task 4: the live chat HTTP surface (``api/live_chat.py``)
mounted behind ``CHAT_MODE=live``, and the app-factory mount switch itself
(``poseidon/api/app.py``).

Most of this file was OFFLINE through Phase 9, mirroring
``test_chat_orchestrator.py``'s own discipline: a real ``create_app``
(never both routers mounted -- see
``test_default_settings_app_mounts_mock_not_live_chat`` /
``test_live_settings_app_mounts_live_chat_not_mock``), then, for the turn-
streaming tests, ``app.state.data_client``/``app.state.run_log_writer``
swapped for a fake data client and a writer double AFTER construction.
``FakeDataClient``/``RecordingWriter`` are imported straight from
``test_chat_orchestrator.py`` rather than re-derived: the flagship scenario
below is the IDENTICAL scripted turn that suite already pins frame-by-frame,
so reusing its fixtures is what proves the HTTP layer reproduces the exact
same numbers the orchestrator-level suite already established.

**Phase 10 Task 3 (the cutover): most of this file is pg-marked now.**
Every route that touches a conversation now goes through
:class:`~poseidon.core.chat.history.HistoryStore` (real Postgres, RLS-
scoped) instead of the deleted in-memory ``TranscriptStore`` -- there is no
offline double for that anymore (the same reasoning
``test_history_store.py``'s own module docstring gives for its pg half).
Tests that only inspect ``app.state``/hit ``/api/skills`` (the mount-switch,
wiring, and tool-registry tests below) never touch history at all and stay
OFFLINE, unmarked, exactly as before. Every OTHER test that used to dispatch
against an ad hoc, never-created conversation id (``"conv-1"``, ``"conv-
retry"``, ``"conv-crash"``, ...) now creates a REAL conversation first via
``POST /api/conversations`` and uses its real id: ``UserHistory.append_
user_message`` raises ``LookupError`` -- mapped to a 404 -- for a ``cid``
that was never created, closing the old TranscriptStore-era auto-vivify
asymmetry this file's docstring used to document (see ``api/live_chat.py``'s
own module docstring, "A conversation that does not exist... now 404s at
send time too"). ``FakeDataClient``/``_ExplodingDataClient`` overrides are
kept exactly as before for every adapted test -- only ``database_url``
switches to a real, migrated Postgres (:func:`pg_database_url`), so every
pinned number in the flagship scenario stays byte-identical; only the
PERSISTENCE layer underneath it is now real.

SSE frames are parsed with the same discipline ``test_mock_chat.py``'s own
``read_sse`` helper uses (httpx ASGI transport, ``client.stream(...)``,
splitting ``event: ``/``data: `` lines) -- the wire format is pinned
byte-for-byte identical to the mock's own ``_sse()`` (see ``events.py``'s
module docstring), so the same parsing works unchanged against either.
"""

import json
import logging
import os
import uuid

import httpx
import psycopg
import pytest
from sqlalchemy import text

from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.llm.roles import RoleClient
from poseidon.core.skills.registry import SkillRegistry
from tests.test_chat_orchestrator import REGISTRY, FakeDataClient, RecordingWriter

# ===========================================================================
# pg availability -- mirrors test_history_store.py's/test_history_cutover.
# py's own pg fixture exactly (this file has no conftest.py to share one
# from; every pg-marked test module in this codebase defines its own).
# ===========================================================================

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0004)"


@pytest.fixture
def pg_database_url() -> str:
    """``DATABASE_URL``, or a SKIP with an actionable reason -- see
    ``test_history_store.py``'s own ``pg_engine`` for the identical
    reachability/migration check, adapted here to hand back the DSN string
    rather than a raw ``Engine`` (this file drives everything through a
    real ``create_app()``, never ``HistoryStore`` directly)."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            f"DATABASE_URL is not set - pg live chat tests need a Postgres: "
            f"{_UP_HINT}, {_MIGRATE_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.conversations')")
                if cur.fetchone()[0] is None:
                    pytest.skip(f"conversations does not exist - {_MIGRATE_HINT}")
    except psycopg.Error as exc:
        pytest.skip(
            f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}"
        )
    return dsn


def _json_lines(text: str) -> list[dict]:
    """Phase 11 Task 2: every line of ``text`` that parses as JSON, in
    order -- skips any that does not. ``_build_tool_registry``'s
    "research transport: ..." boot line now goes out through
    ``core/obs.py`` as JSON (no bare ``print`` left), so the two capsys-
    based tests below parse structured fields instead of grepping raw
    text; a defensive skip on a non-JSON line costs nothing."""
    records = []
    for line in text.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def _dev_user(name: str = "sse") -> str:
    """A fresh, run-unique ``X-Dev-User`` act-as value -- mirrors
    ``test_me_routes.py``'s own ``_dev_user`` helper exactly (that file's
    module docstring: "fresh, run-unique act-as identities... re-running
    this suite against the same long-lived dev Postgres must never
    collide with a previous run's rows").

    F2 fix (2026-08-05 walkthrough): every pg test below that dispatches a
    real turn, or writes through ``ProfileStore``/``MemoryStore``/
    ``OutboxStore``, now poses as one of these throwaway identities
    instead of falling through to ``DisabledProvider``'s fixed default
    (``sub="dev|local"``) -- the real, shared identity Carlos's own
    browser resolves to. See ``core/identity.py``'s own
    ``DisabledProvider.resolve``: any ``X-Dev-User`` header sanitizes to
    ``sub="dev|{value}"``, so this file's writes now land on a unique
    ``dev|sse-<hex>``-shaped sub every run, never the shared one -- no
    cleanup required, the same "leave it behind, it's not a real user"
    convention ``test_me_routes.py``'s own throwaway ``alice``/``bob``
    identities already rely on."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _headers(user: str) -> dict[str, str]:
    return {"X-Dev-User": user}


async def _create_conversation(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    """A real conversation id via ``POST /api/conversations`` -- every
    adapted test in this file that used to dispatch against an ad hoc
    string now creates one for real first (module docstring's "most of
    this file is pg-marked now"). ``headers`` is REQUIRED (no default) --
    F2's own fix (see ``_dev_user`` above): a caller cannot forget to pass
    a run-unique act-as identity and silently fall through to the shared
    ``dev|local`` one."""
    r = await client.post("/api/conversations", headers=headers)
    return r.json()["conversation"]["id"]

# U+2014 EM DASH, written as an escape (not a typed literal) -- the same
# convention every earlier Phase 4/5/6 suite uses (see test_chat_orchestrator.
# py's own module docstring): an em dash, an en dash and a hyphen are visually
# indistinguishable in most editors, and this file must stay pure ASCII on
# disk regardless (house rule: backend .py files are ASCII-only).
_EM_DASH = chr(0x2014)

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

_METRIC_QUERY_DESCRIPTION = REGISTRY.get("data_qa.metric_query").description
# Phase 7 Task 4: research.web_research joined the real registry as its
# second enabled skill -- see test_get_skills_returns_registry_backed_shape
# below.
_RESEARCH_DESCRIPTION = REGISTRY.get("research.web_research").description
# Phase 8 Task 4: both customer_insight brief skills join as the registry's
# third and fourth entries (customer_insight sorts before data_qa).
_EXISTING_CUSTOMER_BRIEF_DESCRIPTION = REGISTRY.get(
    "customer_insight.existing_customer_brief"
).description
_NEW_PROSPECT_BRIEF_DESCRIPTION = REGISTRY.get("customer_insight.new_prospect_brief").description


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        _env_file=None,
        database_url=_PLACEHOLDER_DSN,
        s3_bucket="poseidon-artifacts",
        llm_mode="stub",
        llm_profile="bedrock",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_app():
    from poseidon.api.app import create_app

    return create_app(_settings())


def _live_app(*, data_client=None, writer=None, **settings_overrides):
    """A ``chat_mode="live"`` app, with ``app.state.data_client``/
    ``app.state.run_log_writer`` swapped for test doubles when given -- the
    same post-construction substitution ``test_dev_runner.py`` already uses
    for ``app.state.skill_registry``. ``**settings_overrides`` forwards
    additional ``Settings`` fields (Phase 6 Task 5 amendment: e.g.
    ``data_backend="snowflake"`` for the guard tests below) -- every
    call site above this comment passes none, so their behavior is
    unchanged."""
    from poseidon.api.app import create_app

    app = create_app(_settings(chat_mode="live", **settings_overrides))
    if data_client is not None:
        app.state.data_client = data_client
    if writer is not None:
        app.state.run_log_writer = writer
    return app


async def read_sse(
    client: httpx.AsyncClient,
    cid: str,
    text: str,
    client_turn_key: str | None,
    headers: dict[str, str] | None = None,
):
    """Mirrors ``test_mock_chat.py``'s own ``read_sse`` helper exactly --
    the wire format is pinned byte-identical (``events.py``'s module
    docstring), so the same parsing logic applies unchanged. ``headers``
    is F2's own fix (see ``_dev_user`` above): every caller IN THIS FILE
    now passes a run-unique act-as identity rather than silently
    dispatching as the shared ``dev|local`` default. It stays OPTIONAL
    (default ``None``, forwarded to ``httpx`` as-is -- no extra header
    sent) rather than required, because ``tests/test_chat_e2e_scripted.py``
    imports this exact function and calls it header-less; that file is
    outside F2's sanctioned scope (not named in the fix plan's file list)
    and is disclosed, unfixed, in this task's own report as a same-shaped
    gap for a follow-up task, not silently folded into this one."""
    events = []
    body = {"text": text}
    if client_turn_key is not None:
        body["client_turn_key"] = client_turn_key
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json=body, headers=headers
    ) as response:
        assert response.status_code == 200
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: ") :])))
    return events


# ===========================================================================
# App-factory mount switch: mock and live are never mounted together
# ===========================================================================


def test_default_settings_app_mounts_mock_not_live_chat():
    """``chat_mode`` defaults to "mock" -- NOTHING changes for an existing
    env: the app exposes mock_chat's own routes and none of live_chat's."""
    app = _mock_app()
    paths = app.openapi()["paths"]
    assert "/api/conversations" in paths
    assert "/api/skills" not in paths


def test_live_mode_app_mounts_live_chat_not_mock():
    app = _live_app()
    paths = app.openapi()["paths"]
    assert "/api/skills" in paths
    assert "/api/conversations/{cid}/messages" in paths
    # Task 5 amendment: live_chat.py now serves the SAME four bootstrap
    # paths mock_chat.py does (create/list conversations, transcript,
    # feedback) -- this used to assert the OPPOSITE, back when that gap was
    # still open (see live_chat.py's own module docstring, "Task 5
    # amendment: the live bootstrap routes"). The mutual exclusivity this
    # test's NAME promises is enforced by app.py's own if/else mount switch
    # (never both app.include_router calls run), not by the two modes
    # serving disjoint path SETS anymore -- proven instead by GET
    # /api/skills, which mock_chat.py never defines at all
    # (test_default_settings_app_mounts_mock_not_live_chat's own assertion,
    # the mirror-image direction).
    assert "/api/conversations" in paths
    assert "/api/messages/{mid}/feedback" in paths


def test_live_mode_app_wires_the_expected_app_state():
    app = _live_app()
    assert hasattr(app.state, "skill_registry")
    assert "data_qa.metric_query" in app.state.skill_registry.skill_ids
    # Phase 10 Task 3: conversation_state_store/transcript_store are gone --
    # history_store/feedback_store/db_engine replace them (app.py's own
    # _wire_live_chat docstring, "Phase 10 Task 3: ONE Engine per process").
    assert hasattr(app.state, "history_store")
    assert hasattr(app.state, "feedback_store")
    assert hasattr(app.state, "db_engine")
    assert not hasattr(app.state, "conversation_state_store")
    assert not hasattr(app.state, "transcript_store")
    assert hasattr(app.state, "role_client")
    assert hasattr(app.state, "prompt_registry")
    assert hasattr(app.state, "data_client")
    # Phase 7 Task 4.
    assert hasattr(app.state, "tool_registry")
    # DATABASE_URL is always syntactically valid here (a placeholder host,
    # never a malformed DSN), so construction succeeds and the writer is a
    # real, constructed one -- see
    # test_live_app_construction_fails_fast_when_the_engine_cannot_be_built
    # below for the disclosed hard-failure branch (Phase 10 Task 3: history
    # is no longer optional, so a malformed DATABASE_URL is now a boot
    # failure rather than a silently-absent writer).
    assert app.state.run_log_writer is not None


def test_stub_llm_mode_installs_a_fixture_research_tool(capsys):
    """Phase 7 Task 4 (AMENDED post-Task-2): ``LLM_MODE=stub`` (``_settings
    ()``'s own default) installs a ``FixtureResearchTool`` override --
    never a live transport, no matter what ``PERPLEXITY_API_KEY`` happens
    to be set in the ambient environment (key PRESENCE is the wrong gate --
    see ``app.py``'s own ``_build_tool_registry`` docstring).

    Phase 11 Task 2 adaptation (disclosed): the boot line is now one JSON
    line through ``core/obs.py``, not a bare ``print`` -- adapted from a
    raw substring check against captured stdout text to a structural
    assertion over the parsed record's ``event``/``context`` fields. The
    pinned label text (``"fixture (llm_mode=stub)"``) survives verbatim as
    ``context["transport"]``.
    """
    app = _live_app()

    result = app.state.tool_registry.research.search(query="q", schema_name="web_research")

    assert result.transport == "fixture"
    records = _json_lines(capsys.readouterr().out)
    transport_lines = [r for r in records if r["event"] == "research_transport_resolved"]
    assert len(transport_lines) == 1
    assert transport_lines[0]["context"]["transport"] == "fixture (llm_mode=stub)"


def test_live_llm_mode_resolves_the_configured_perplexity_transport(capsys):
    """The other half of the AMENDED gate: ``LLM_MODE=live`` leaves
    resolution to ``ToolServerRegistry`` itself, per
    ``TOOL_TRANSPORT_PERPLEXITY`` (default ``"direct"``) -- proven WITHOUT
    ever firing a live call: only the CONSTRUCTED adapter's type is
    checked, never ``.search()`` (``PerplexityDirectAdapter``'s own lazy-
    client contract already proves construction alone touches no network).

    Phase 11 Task 2 adaptation (disclosed): same shape as the stub-mode
    test above -- the pinned label text (``"direct (llm_mode=live)"``)
    survives verbatim as ``context["transport"]`` on the parsed JSON line.
    """
    from poseidon.mcp.perplexity.adapter import PerplexityDirectAdapter

    app = _live_app(llm_mode="live")

    assert isinstance(app.state.tool_registry.research, PerplexityDirectAdapter)
    records = _json_lines(capsys.readouterr().out)
    transport_lines = [r for r in records if r["event"] == "research_transport_resolved"]
    assert len(transport_lines) == 1
    assert transport_lines[0]["context"]["transport"] == "direct (llm_mode=live)"


def test_live_mode_local_deploy_discovers_the_skill_registry_exactly_once(monkeypatch):
    """Fix round 1, MINOR M1: ``chat_mode="live"`` + ``deploy_mode="local"``
    (the DEFAULT ``deploy_mode`` -- every ``_live_app()`` call in this file
    already exercises this exact combination) used to call ``SkillRegistry.
    discover()`` TWICE at boot -- once in ``_wire_live_chat``, once again in
    the local-mode dev-runner block -- building two independent,
    structurally-identical registries and silently discarding the first.
    One boot of a local live app is one discovery walk."""
    calls: list[object] = []
    original_discover = SkillRegistry.discover  # already bound to SkillRegistry

    def counting_discover(*args, **kwargs):
        calls.append(None)
        return original_discover(*args, **kwargs)

    monkeypatch.setattr(SkillRegistry, "discover", counting_discover)

    _live_app()

    assert len(calls) == 1


def test_live_app_construction_fails_fast_when_the_engine_cannot_be_built():
    """Phase 10 Task 3 adaptation of the old ``test_run_log_writer_is_none_
    when_the_engine_cannot_be_built`` (renamed: the OLD assertion --
    ``run_log_writer is None``, booting anyway -- no longer holds). ``create_
    engine`` -- unlike ``SyntheticDataClient.__init__`` -- DOES eagerly parse
    its URL argument, so a value ``Settings.database_url``'s own "not blank"
    validator accepts but which is not a URL SQLAlchemy can parse at all is
    the one realistic way this branch is reached (see ``app.py``'s own
    ``_wire_live_chat`` docstring, "A malformed DATABASE_URL is now a hard
    boot failure"). Before this task, ``RunLogWriter`` alone depended on
    this engine and its own construction failure was caught and logged
    non-fatally (an OPTIONAL run log); now ``HistoryStore`` -- with no
    "disabled" mode of its own -- shares the identical engine, so the SAME
    failure can no longer be swallowed without leaving ``app.state.history_
    store`` unset for every request to crash on individually instead of at
    boot, once, loudly."""
    from sqlalchemy.exc import ArgumentError

    with pytest.raises(ArgumentError):
        _live_app_with_database_url("not-a-url-at-all")


def _live_app_with_database_url(database_url: str):
    from poseidon.api.app import create_app

    return create_app(_settings(chat_mode="live", database_url=database_url))


# ===========================================================================
# POST /api/conversations/{cid}/messages -- the flagship scripted turn,
# reproduced byte-for-byte through the real HTTP surface
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_live_turn_streams_the_flagship_frame_sequence_and_table_and_proof_parts(
    pg_database_url,
):
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    headers = _headers(_dev_user("flagship"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client, headers)
        events = await read_sse(
            client,
            cid,
            "Top GP customers for Port of Singapore in April 2026",
            "ctk-1",
            headers,
        )

    names = [name for name, _data in events]
    assert names == ["accepted", "tool", "tool", "part", "part", "token", "done"]

    payloads = [data for _name, data in events]
    # envelope on every frame: turn_id/message_id/event_seq, one turn_id,
    # strictly increasing event_seq starting at 1 (doc 01 section 5).
    assert all({"turn_id", "message_id", "event_seq"} <= set(p) for p in payloads)
    turn_ids = {p["turn_id"] for p in payloads}
    assert len(turn_ids) == 1
    turn_id = turn_ids.pop()
    seqs = [p["event_seq"] for p in payloads]
    assert seqs == list(range(1, len(payloads) + 1))

    table_payload = payloads[3]
    assert table_payload["kind"] == "table"
    assert table_payload["payload"] == {
        "columns": ["Customer", "Gross Profit"],
        "rows": [
            ["Northstar Lines", 412000],
            ["Blue Anchor Marine", 268500],
            ["Crestline Freight", 155250],
        ],
    }

    proof_payload = payloads[4]
    assert proof_payload["kind"] == "proof"
    assert proof_payload["payload"]["lines"][0] == "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V"
    assert "Rows: 3" in proof_payload["payload"]["lines"]

    token_payload = payloads[5]
    assert token_payload["text"].startswith("Certified answer for SINGAPORE")

    # Run-log turn-id unification, proven at the HTTP layer: the turn_id on
    # every SSE frame IS the turn_run_id the writer double actually received.
    assert len(writer.start_turn_calls) == 1
    assert writer.start_turn_calls[0]["turn_run_id"] == turn_id
    assert len(writer.finalize_calls) == 1
    assert writer.finalize_calls[0]["turn_run_id"] == turn_id


class _RecordingRoleClientStub:
    """Wraps the REAL ``DevDeterministicRouter`` so this test still
    exercises genuine routing decisions -- only ``system`` is intercepted.
    Mirrors ``test_chat_orchestrator.py``'s own ``_RecordingStub`` exactly
    (duplicated, not imported -- a role_client double is new to THIS file,
    matching the established "each test module owns its own private
    helpers" convention ``test_entry_orchestration.py``'s module docstring
    states explicitly for every OTHER small helper it duplicates rather
    than imports)."""

    def __init__(self) -> None:
        self._inner = DevDeterministicRouter()
        self.systems: list[str] = []

    def invoke(self, *, system, messages, tools, model, params):
        self.systems.append(system)
        return self._inner.invoke(
            system=system, messages=messages, tools=tools, model=model, params=params
        )


@pytest.mark.pg
@pytest.mark.anyio
async def test_live_turn_injects_real_instruction_and_memory_and_touches_the_outbox(
    pg_database_url,
):
    """Phase 13 Task 2 (plan amendment, commit bf43d34): the one thing
    ``test_orchestrator_personalization.py``'s offline suite cannot prove on
    its own -- that a REAL HTTP turn, through ``api/live_chat.py``'s own
    ``execute_turn(...)`` call (now threading ``app.state.profile_store``/
    ``.memory_store``/``.outbox_store`` straight through, this same task's
    amended scope), actually receives the injected instruction/memory in
    the prompt the provider sees, and actually touches the conversation's
    ``memory_outbox`` row.

    Mirrors this file's own flagship pg test's app-construction shape
    exactly (``_live_app`` + a real Postgres ``database_url``), with
    ``app.state.role_client`` ALSO swapped post-construction -- the same
    "swap an app.state object after ``create_app`` returns" substitution
    already established here for ``data_client``/``run_log_writer`` -- for
    a recording stub that captures the REAL ``system`` text sent to the
    provider, so this test asserts on captured prompt TEXT (this task's own
    standard throughout, not "a store method was called").

    F2 fix (2026-08-05 walkthrough): this test used to hard-code
    ``DISABLED_DEFAULT_USER.sub`` ("dev|local") -- on a docstring claim
    that there was "no way to seed a different user's instruction/memory
    for an HTTP-level test without a real auth flow this phase does not
    build". That claim was FALSE: ``identity_mode="disabled"`` (this
    file's default, like every other pg test here) honors the
    ``X-Dev-User`` act-as header exactly like ``test_me_routes.py``
    already relies on for every one of its own tests (``core/identity.
    py``'s ``DisabledProvider.resolve``). This test now poses as a fresh,
    run-unique ``_dev_user(...)`` identity (see that helper's own
    docstring), and seeds ITS store rows only -- never the real, shared
    ``dev|local`` sub Carlos's own browser resolves to. No ``finally``
    cleanup is needed any more: a throwaway act-as sub is never reused
    across runs, the same "leave it behind, it's not a real user"
    convention ``test_me_routes.py``'s own ``alice``/``bob`` helpers
    already use.
    """
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    stub = _RecordingRoleClientStub()
    app.state.role_client = RoleClient(app.state.settings, providers={"stub": stub})

    user = _dev_user("instr")
    headers = _headers(user)
    user_sub = f"dev|{user}"
    instruction = "Always show GP in USD thousands."
    memory_entry = {
        "type": "preference",
        "statement": "Prefers concise, no-fluff answers.",
        "source_conversation_id": "conv-seed",
        "at": "2026-01-01T00:00:00",
    }
    expected_memory_markdown = (
        "- [preference] Prefers concise, no-fluff answers. "
        "(source: conv-seed, at: 2026-01-01T00:00:00)"
    )
    app.state.profile_store.for_user(user_sub).put(instruction)
    app.state.memory_store.for_user(user_sub).write_version([memory_entry], created_by="user")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client, headers)
        events = await read_sse(
            client,
            cid,
            "Top GP customers for Port of Singapore in April 2026",
            "ctk-instr-1",
            headers,
        )

    names = [name for name, _data in events]
    assert names == ["accepted", "tool", "tool", "part", "part", "token", "done"]

    # ``stub.systems`` also captures live_chat.py's OWN turn-one title
    # generation call (``_finalize_turn``'s own ``title_for(...)``,
    # role "utility") -- a completely different, unrelated prompt that
    # never carries assemble_system's own sections at all, since the
    # SAME ``app.state.role_client`` (now the recording stub) answers
    # every role, not just "router". ``=== CONVERSATION STATE ===`` is
    # assemble_system's own last-section header (core/llm/prompts.py) --
    # present ONLY on a real router/synthesis render, never the title
    # prompt -- so it is what isolates the calls this assertion cares
    # about from that unrelated one.
    router_systems = [s for s in stub.systems if "=== CONVERSATION STATE ===" in s]
    assert router_systems  # at least one real router call happened
    assert len(set(router_systems)) == 1  # one system per turn, reused across iterations
    for system in router_systems:
        assert f"=== USER INSTRUCTION ===\n{instruction}" in system
        assert f"=== MEMORY ===\n{expected_memory_markdown}" in system

    # The outbox touch: memory_outbox has no store-level read method
    # (ConversationOutbox.touch is write-only by design -- Task 1's own
    # interface), so this reads the row directly, the same raw-query
    # verification style test_personalization_stores.py's own touch()
    # tests already use.
    with app.state.db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, attempts, last_turn_at FROM memory_outbox "
                "WHERE conversation_id = :c"
            ),
            {"c": cid},
        ).first()
    assert row is not None
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.last_turn_at is not None


@pytest.mark.pg
@pytest.mark.anyio
async def test_client_turn_key_retry_gets_pinned_duplicate_turn_error_and_no_second_dispatch(
    pg_database_url,
):
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    headers = _headers(_dev_user("retry"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client, headers)
        first = await read_sse(
            client,
            cid,
            "Top GP customers for Port of Singapore in April 2026",
            "ctk-1",
            headers,
        )
        second = await read_sse(
            client,
            cid,
            "Top GP customers for Port of Singapore in April 2026",
            "ctk-1",
            headers,
        )

    first_names = [name for name, _data in first]
    assert first_names == ["accepted", "tool", "tool", "part", "part", "token", "done"]

    names = [name for name, _data in second]
    assert names == ["accepted", "error"]
    error_data = second[1][1]
    assert error_data["code"] == "duplicate_turn"
    assert error_data["message"] == (
        "this turn was already processed " + _EM_DASH + " refresh to load the conversation"
    )

    # No re-dispatch: exactly one turn's worth of tool/finalize rows exist,
    # even though the client sent the request twice.
    assert len(writer.append_tool_calls) == 1
    assert len(writer.finalize_calls) == 1
    # Both attempts were recorded by start_turn (the idempotent-insert path
    # is exercised both times), but only the first one created a row.
    assert len(writer.start_turn_calls) == 2


# ===========================================================================
# Fix round 1, REQUIRED F1 -- an unhandled exception mid-turn (the realistic
# case: a Bedrock network hiccup once LLM_MODE=live) must still end the
# stream cleanly with ONE pinned error frame, finalize the run-log row as
# failed, and leave the app healthy for the next request.
# ===========================================================================


def _crashing_execute_turn(**kwargs):
    """Stands in for the real ``execute_turn``: emits a partial turn (an
    ``accepted`` frame and one token, exactly like a real turn that got as
    far as streaming some of its answer) then raises, unhandled -- the
    realistic shape of a mid-turn provider failure (a network hiccup calling
    Bedrock), not a failure at the very first line."""
    sink = kwargs["sink"]
    sink.accepted(1)
    sink.push_token("partial reply")
    raise RuntimeError("simulated bedrock network hiccup")


@pytest.mark.pg
@pytest.mark.anyio
async def test_unhandled_exception_mid_turn_emits_pinned_internal_error_and_ends_stream_cleanly(
    pg_database_url, monkeypatch, caplog
):
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    monkeypatch.setattr("poseidon.api.live_chat.execute_turn", _crashing_execute_turn)
    headers = _headers(_dev_user("crash"))

    transport = httpx.ASGITransport(app=app)
    with caplog.at_level(logging.ERROR, logger="poseidon.api.live_chat"):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            cid = await _create_conversation(client, headers)
            events = await read_sse(client, cid, "hello", None, headers)

    # The client sees the partial turn, then ONE pinned error frame, then a
    # clean stream end -- never a raw protocol error (no more frames, no
    # exception raised out of read_sse/the ASGI transport).
    names = [name for name, _data in events]
    assert names == ["accepted", "token", "error"]
    turn_id = events[0][1]["turn_id"]
    error_data = events[2][1]
    assert error_data["code"] == "internal_error"
    assert error_data["message"] == (
        "the turn failed unexpectedly " + _EM_DASH + " the error has been logged"
    )

    # The run-log row is finalized as failed exactly once, keyed by the SAME
    # turn_id every frame carried (the turn-id unification amendment applies
    # on the crash path too), never left orphaned at status='running'.
    assert len(writer.finalize_calls) == 1
    finalize = writer.finalize_calls[0]
    assert finalize["turn_run_id"] == turn_id
    assert finalize["status"] == "error"
    assert finalize["message_id"] is None
    assert finalize["answer_summary"] is None
    assert finalize["input_tokens"] == 0
    assert finalize["output_tokens"] == 0
    assert isinstance(finalize["latency_ms"], int)
    assert finalize["latency_ms"] >= 0
    assert finalize["error"]["title"] == "internal_error"
    assert "RuntimeError" in finalize["error"]["detail"]
    assert "simulated bedrock network hiccup" in finalize["error"]["detail"]

    # Logged at ERROR, once, naming the exception type.
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "RuntimeError" in errors[0].message

    # A follow-up request on the SAME app (real execute_turn restored)
    # succeeds -- the crash left nothing wedged. A SECOND real conversation
    # (never the crashed one): a fresh cid, exactly like a real client
    # opening a new chat, not a retry against the one that just failed.
    monkeypatch.undo()
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid2 = await _create_conversation(client, headers)
        follow_up = await read_sse(
            client, cid2, "Top GP customers for Port of Singapore in April 2026", None, headers
        )
    assert [name for name, _data in follow_up] == [
        "accepted",
        "tool",
        "tool",
        "part",
        "part",
        "token",
        "done",
    ]


@pytest.mark.pg
@pytest.mark.anyio
async def test_unhandled_exception_with_no_writer_still_ends_stream_cleanly(
    pg_database_url, monkeypatch
):
    """``writer=None`` (simulating no DATABASE_URL configured for the run
    log specifically) must not change the crash-handling shape -- only the
    run-log gains no finalize call, mirroring execute_turn's own `writer is
    not None` guard convention throughout. ``app.state.run_log_writer`` is
    overridden to ``None`` AFTER construction -- Phase 10 Task 3 no longer
    has a way to construct a live app with a genuinely absent writer
    (history and the run log share one mandatory engine now), so this is
    the one remaining way to exercise that guard at all."""
    app = _live_app(data_client=FakeDataClient(), database_url=pg_database_url)
    app.state.run_log_writer = None
    monkeypatch.setattr("poseidon.api.live_chat.execute_turn", _crashing_execute_turn)
    headers = _headers(_dev_user("crash-nowriter"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client, headers)
        events = await read_sse(client, cid, "hello", None, headers)

    assert [name for name, _data in events] == ["accepted", "token", "error"]


# ===========================================================================
# GET /api/skills -- registry-backed [{id, label, description}]
# ===========================================================================


@pytest.mark.anyio
async def test_get_skills_returns_registry_backed_shape():
    """Phase 8 Task 4: both customer_insight brief skills join the registry
    (``registry.skill_ids``' own sorted-by-id order -- "customer_insight" <
    "data_qa" < "research", and within customer_insight,
    "existing_customer_brief" < "new_prospect_brief" -- so the two brief
    skills now come FIRST, ahead of metric_query/web_research)."""
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills")

    assert r.status_code == 200
    body = r.json()
    assert body == [
        {
            "id": "customer_insight.existing_customer_brief",
            "label": "Existing customer brief",
            "description": _EXISTING_CUSTOMER_BRIEF_DESCRIPTION,
        },
        {
            "id": "customer_insight.new_prospect_brief",
            "label": "New prospect brief",
            "description": _NEW_PROSPECT_BRIEF_DESCRIPTION,
        },
        {
            "id": "data_qa.metric_query",
            "label": "Metric query",
            "description": _METRIC_QUERY_DESCRIPTION,
        },
        {
            "id": "research.web_research",
            "label": "Web research",
            "description": _RESEARCH_DESCRIPTION,
        },
    ]


# ===========================================================================
# The live bootstrap routes -- mock_chat.py's own conversation create/list/
# transcript/feedback shapes. Phase 10 Task 3: backed by Postgres-backed
# HistoryStore now, not the deleted in-memory TranscriptStore -- pg-marked
# below wherever a route actually touches it.
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_post_conversations_returns_the_same_opener_shape_as_mock(pg_database_url):
    """Same wire shape mock_chat.py's own create_conversation returns --
    the frontend's bootstrap() reads conversation.id/opener.parts and does
    not care which mode produced them."""
    app = _live_app(database_url=pg_database_url)
    headers = _headers(_dev_user("opener"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations", headers=headers)

    assert r.status_code == 201
    body = r.json()
    assert set(body["conversation"]) == {"id", "title"}
    opener = body["opener"]
    assert opener["role"] == "assistant"
    kinds = [p["kind"] for p in opener["parts"]]
    assert kinds == ["text", "chips"]
    options = opener["parts"][1]["payload"]["options"]
    ids = [o["id"] for o in options]
    assert ids == ["existing_customer", "new_prospect"]


@pytest.mark.pg
@pytest.mark.anyio
async def test_opener_flow_chips_carry_the_d19_pinned_entry_phrases_as_send_text(
    pg_database_url,
):
    """Phase 8 Task 5: the P6 send_text mechanism (ChipsPart.tsx's own
    ``option.send_text ?? option.label``), now on the opener's flow chips
    too -- clicking either one sends the EXACT pinned phrase
    ``orchestrator.py``'s own D19 entry branch matches, casefolded-exact,
    rather than the bare "Existing customer"/"New customer prospect"
    labels a human reads on the button."""
    app = _live_app(database_url=pg_database_url)
    headers = _headers(_dev_user("opener-chips"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations", headers=headers)

    options = r.json()["opener"]["parts"][1]["payload"]["options"]
    assert options == [
        {
            "id": "existing_customer",
            "label": "Existing customer",
            "send_text": "start an existing-customer brief",
        },
        {
            "id": "new_prospect",
            "label": "New customer prospect",
            "send_text": "start a new-prospect brief",
        },
    ]


@pytest.mark.pg
@pytest.mark.anyio
async def test_get_conversations_lists_newest_first(pg_database_url):
    """Phase 10 Task 3: the envelope changed from a bare ``{"conversations":
    [...]}`` array to ``{"items": [...], "next_cursor": ...}`` -- Task 4
    (the frontend) follows immediately; see api/live_chat.py's own module
    docstring for the disclosed, scoped gap in each item's own shape
    (``{"id", "title"}`` today, not yet ``mode``/``updated_at``)."""
    app = _live_app(database_url=pg_database_url)
    headers = _headers(_dev_user("list"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        c1 = (await client.post("/api/conversations", headers=headers)).json()["conversation"]["id"]
        c2 = (await client.post("/api/conversations", headers=headers)).json()["conversation"]["id"]
        body = (await client.get("/api/conversations", headers=headers)).json()

    assert set(body) == {"items", "next_cursor"}
    assert [c["id"] for c in body["items"][:2]] == [c2, c1]


@pytest.mark.pg
@pytest.mark.anyio
async def test_get_conversations_limit_out_of_range_is_422_not_500(pg_database_url):
    """Final-review wave, I-1: limit<=0 used to reach history.py's own
    keyset-pagination slicing with an EMPTY page while `rows` came back
    non-empty, raising an unhandled IndexError on `page[-1]` -- a bare 500
    with no RFC-7807 body (see UserHistory.list_conversations). limit=0
    needs at least one visible conversation to trip `len(rows) > limit`;
    limit=-1 trips the identical branch UNCONDITIONALLY (`LIMIT 0` always
    returns zero rows, and `0 > -1` is True regardless of table content).
    limit=201 exercises the new upper bound (the old code accepted any
    int, unbounded). FastAPI's own Query(ge=1, le=200) now rejects all
    three before the route body ever runs -- 422, never 500."""
    app = _live_app(database_url=pg_database_url)
    headers = _headers(_dev_user("limit"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        # >=1 row, for limit=0's own branch
        await client.post("/api/conversations", headers=headers)

        responses = [
            await client.get("/api/conversations", params={"limit": bad_limit}, headers=headers)
            for bad_limit in (0, -1, 201)
        ]

    for response in responses:
        assert response.status_code == 422
        offending = {err["loc"][-1] for err in response.json()["detail"]}
        assert "limit" in offending


@pytest.mark.anyio
async def test_get_messages_404_for_a_conversation_id_never_seen():
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/conversations/never-seen/messages")

    assert r.status_code == 404


@pytest.mark.pg
@pytest.mark.anyio
async def test_get_messages_returns_the_opener_right_after_create(pg_database_url):
    app = _live_app(database_url=pg_database_url)
    headers = _headers(_dev_user("opener-msgs"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        r = await client.get(f"/api/conversations/{cid}/messages", headers=headers)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "next_cursor"}
    assert [m["role"] for m in body["items"]] == ["assistant"]


@pytest.mark.pg
@pytest.mark.anyio
async def test_get_messages_limit_out_of_range_is_422_not_500(pg_database_url):
    """Final-review wave, I-1: the same page[-1] defect in
    UserHistory.get_messages, reached through the OTHER paginated route.
    limit=0 needs the conversation's own opener message (created for free
    by POST /api/conversations) to trip `len(rows) > limit`; limit=-1
    trips it unconditionally; limit=501 exercises the new upper bound --
    same three-value matrix as the conversations-list case above."""
    app = _live_app(database_url=pg_database_url)
    headers = _headers(_dev_user("msgs-limit"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]

        responses = [
            await client.get(
                f"/api/conversations/{cid}/messages", params={"limit": bad_limit}, headers=headers
            )
            for bad_limit in (0, -1, 501)
        ]

    for response in responses:
        assert response.status_code == 422
        offending = {err["loc"][-1] for err in response.json()["detail"]}
        assert "limit" in offending


@pytest.mark.pg
@pytest.mark.anyio
async def test_a_real_turn_is_recorded_into_the_transcript_user_then_assistant_parts(
    pg_database_url,
):
    """The flagship scripted turn, through the real bootstrap-send-reopen
    round trip: create a conversation for real, send a real turn, reopen
    the transcript and see exactly what was streamed -- assistant messages
    are appended from the turn's emitted parts at done-time (the amendment's
    own words), not re-derived some other way."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    headers = _headers(_dev_user("transcript"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        await read_sse(
            client,
            cid,
            "Top GP customers for Port of Singapore in April 2026",
            "ctk-transcript",
            headers,
        )
        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]

    # opener (from create), then the user's question, then the assistant's answer.
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    user_msg, assistant_msg = msgs[1], msgs[2]
    assert user_msg["parts"] == [
        {
            "kind": "text",
            "payload": {"markdown": "Top GP customers for Port of Singapore in April 2026"},
        }
    ]
    kinds = [p["kind"] for p in assistant_msg["parts"]]
    assert kinds == ["tool_event", "table", "proof", "text"]
    tool_event = assistant_msg["parts"][0]["payload"]
    assert tool_event["status"] == "done"
    assert tool_event["tool"] == "data_qa.metric_query"
    assert "turn_id" not in tool_event and "event_seq" not in tool_event
    assert assistant_msg["parts"][1]["payload"]["columns"] == ["Customer", "Gross Profit"]
    assert assistant_msg["parts"][3]["payload"]["markdown"].startswith(
        "Certified answer for SINGAPORE"
    )


# Phase 10 Task 3: the old test_record_transcript_frame_tool_event_position_
# matches_the_live_views_own_rule (a unit-level TranscriptStore/_record_
# transcript_frame test) is DELETED, not merely moved -- its fold-semantics
# assertions were already ported verbatim onto TurnTranscriptBuffer in Task
# 2 (test_history_store.py's own test_turn_transcript_buffer_record_tool_
# event_position_matches_the_live_views_own_rule, which says exactly this).
# _record_transcript_frame's OWN decode-and-dispatch logic (the half Task 2
# explicitly left to this task -- see that test's own docstring, "SSE frame
# decoding is api/live_chat.py's concern, not this store's") stays covered
# by the end-to-end HTTP tests above and below, which exercise it for real
# on every real turn/clarify they drive.


@pytest.mark.pg
@pytest.mark.anyio
async def test_a_clarify_turn_is_recorded_into_the_transcript_as_chips_then_text(pg_database_url):
    """Final-review wave item 6: contrast with
    ``test_post_conversations_returns_the_same_opener_shape_as_mock``'s own
    OPENER kinds (``["text", "chips"]``) -- the clarify TURN's own
    transcript kinds are the opposite order, chips first then text, matching
    ``orchestrator.py``'s own ``_finish_clarify`` push order (the chips part,
    then the "did you mean" text part)."""
    app = _live_app(
        data_client=FakeDataClient(), writer=RecordingWriter(), database_url=pg_database_url
    )
    headers = _headers(_dev_user("clarify"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        await read_sse(client, cid, "gp for Meridiann in April 2026", None, headers)
        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]

    assistant_msg = msgs[-1]
    kinds = [p["kind"] for p in assistant_msg["parts"]]
    assert kinds == ["chips", "text"]
    # The clarification chips carry the same "for <name>" send_text
    # orchestrator.py now emits (final-review wave item 2) -- the transcript
    # records the part payload verbatim, envelope stripped.
    first_option = assistant_msg["parts"][0]["payload"]["options"][0]
    assert first_option["send_text"] == f"for {first_option['label']}"


@pytest.mark.pg
@pytest.mark.anyio
async def test_send_message_to_a_never_created_conversation_id_404s(pg_database_url):
    """Phase 10 Task 3 repurposes the old ``test_streaming_route_auto_
    vivifies_transcript_for_an_unregistered_conversation_id``: TranscriptStore's
    own auto-vivify behavior that test exercised is GONE, by necessity, not
    merely by choice -- ``messages.conversation_id`` is a real foreign key
    now, so a bare INSERT naming a conversation nobody created cannot
    silently succeed the way a dict write always could. The NEW, opposite
    behavior (see api/live_chat.py's own module docstring, "A conversation
    that does not exist... now 404s at send time too") is worth the same
    kind of direct coverage the old test gave its own behavior, rather than
    simply deleting it and losing the scenario."""
    app = _live_app(
        data_client=FakeDataClient(), writer=RecordingWriter(), database_url=pg_database_url
    )
    headers = _headers(_dev_user("never-created"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            "/api/conversations/never-created/messages", json={"text": "hello"}, headers=headers
        )

    assert r.status_code == 404


@pytest.mark.pg
@pytest.mark.anyio
async def test_feedback_roundtrip_and_unknown_message_404(pg_database_url):
    # Phase 12 Task 1: a real RunLogWriter, not RecordingWriter -- unlike
    # every OTHER pg test in this module, this one now needs a genuine
    # turn_run row: message_feedback.run_id is NOT NULL REFERENCES turn_run
    # (id) (doc 06 section 7), and RecordingWriter is a pure in-memory
    # double that never inserts one (see that class's own docstring in
    # test_chat_orchestrator.py). Omitting `writer=` here leaves _live_app's
    # underlying create_app()-built app.state.run_log_writer in place --
    # the real writer create_app()/`_wire_live_chat` already constructs.
    app = _live_app(data_client=FakeDataClient(), database_url=pg_database_url)
    headers = _headers(_dev_user("feedback"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None, headers)
        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]
        mid = msgs[-1]["id"]

        r = await client.post(
            f"/api/messages/{mid}/feedback",
            json={"verdict": "down", "comment": "wrong port"},
            headers=headers,
        )
        assert r.status_code == 204
        r = await client.get(f"/api/messages/{mid}/feedback", headers=headers)
        assert r.json() == {"verdict": "down", "comment": "wrong port"}

        r = await client.post(
            f"/api/messages/{mid}/feedback", json={"verdict": "up"}, headers=headers
        )
        assert r.status_code == 204
        r = await client.get(f"/api/messages/{mid}/feedback", headers=headers)
        assert r.json() == {"verdict": "up", "comment": None}

        # "nope" is not even a well-formed uuid -- the SAME fail-closed
        # "malformed id treated exactly like absent" gate core/chat/
        # history.py's own methods use, reached here via the feedback
        # existence gate (api/live_chat.py's own _message_visible) rather
        # than TranscriptStore's now-deleted membership check.
        r = await client.post(
            "/api/messages/nope/feedback", json={"verdict": "up"}, headers=headers
        )
        assert r.status_code == 404
        r = await client.get("/api/messages/nope/feedback", headers=headers)
        assert r.status_code == 404


@pytest.mark.pg
@pytest.mark.anyio
async def test_feedback_null_verdict_clears_a_recorded_vote_then_get_404s(pg_database_url):
    """Un-vote follow-up to Phase 12 (migration 0007): POST verdict=null
    upserts NULL into the SAME row (never a DELETE -- 0006's own D25 NO-
    DELETE-grant decision stays untouched), which GET then reports exactly
    like "never voted" -- 404, indistinguishable by design (this route's
    existing 404-means-no-vote contract, unchanged)."""
    app = _live_app(data_client=FakeDataClient(), database_url=pg_database_url)
    headers = _headers(_dev_user("unvote"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None, headers)
        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]
        mid = msgs[-1]["id"]

        r = await client.post(
            f"/api/messages/{mid}/feedback", json={"verdict": "down"}, headers=headers
        )
        assert r.status_code == 204
        r = await client.get(f"/api/messages/{mid}/feedback", headers=headers)
        assert r.status_code == 200

        r = await client.post(
            f"/api/messages/{mid}/feedback", json={"verdict": None}, headers=headers
        )
        assert r.status_code == 204

        r = await client.get(f"/api/messages/{mid}/feedback", headers=headers)
        assert r.status_code == 404


@pytest.mark.pg
@pytest.mark.anyio
async def test_feedback_null_verdict_on_a_never_voted_message_is_a_noop_204(pg_database_url):
    app = _live_app(data_client=FakeDataClient(), database_url=pg_database_url)
    headers = _headers(_dev_user("noop-unvote"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None, headers)
        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]
        mid = msgs[-1]["id"]

        r = await client.post(
            f"/api/messages/{mid}/feedback", json={"verdict": None}, headers=headers
        )

        assert r.status_code == 204
        assert (
            await client.get(f"/api/messages/{mid}/feedback", headers=headers)
        ).status_code == 404


@pytest.mark.pg
@pytest.mark.anyio
async def test_feedback_invalid_verdict_returns_422(pg_database_url):
    app = _live_app(
        data_client=FakeDataClient(), writer=RecordingWriter(), database_url=pg_database_url
    )
    headers = _headers(_dev_user("bad-verdict"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None, headers)
        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]

        r = await client.post(
            f"/api/messages/{msgs[-1]['id']}/feedback",
            json={"verdict": "sideways"},
            headers=headers,
        )

    assert r.status_code == 422


# ===========================================================================
# Phase 6 Task 5 amendment: the data_backend == "snowflake" guard
# dev_runner.py already has (_build_ctx's own structured 501), adapted to
# this endpoint's SSE shape -- fail loudly with ONE error frame, never
# silently query the synthetic schema instead.
# ===========================================================================


class _ExplodingDataClient:
    """Any method call is a test failure -- proves the guard never reaches
    the data client at all ("never silently query the wrong schema")."""

    def __getattr__(self, name):
        def _boom(*_args, **_kwargs):
            raise AssertionError(
                f"data client method {name!r} must never be called behind the snowflake guard"
            )

        return _boom


@pytest.mark.pg
@pytest.mark.anyio
async def test_snowflake_data_backend_emits_one_structured_error_frame_and_never_touches_data(
    pg_database_url,
):
    app = _live_app(
        data_client=_ExplodingDataClient(),
        writer=RecordingWriter(),
        data_backend="snowflake",
        database_url=pg_database_url,
    )
    headers = _headers(_dev_user("sf-guard"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        events = await read_sse(client, cid, "hello", None, headers)

    assert [name for name, _data in events] == ["error"]
    error_data = events[0][1]
    assert error_data["code"] == "backend not implemented"
    assert "data_backend='snowflake'" in error_data["message"]
    assert "Phase 15" in error_data["message"]


@pytest.mark.pg
@pytest.mark.anyio
async def test_snowflake_guard_still_records_an_empty_assistant_message_in_the_transcript(
    pg_database_url,
):
    app = _live_app(
        data_client=_ExplodingDataClient(),
        writer=RecordingWriter(),
        data_backend="snowflake",
        database_url=pg_database_url,
    )
    headers = _headers(_dev_user("sf-guard-transcript"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (
            await client.post("/api/conversations", headers=headers)
        ).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None, headers)
        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]

    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["parts"] == []


def test_mock_mode_app_still_has_none_of_the_live_bootstrap_routes():
    """Regression guard: chat_mode="mock" is untouched by this amendment --
    mock_chat.py's OWN routes serve /api/conversations already; this
    amendment's code lives entirely behind chat_mode="live"."""
    app = _mock_app()
    paths = app.openapi()["paths"]
    assert "/api/skills" not in paths

"""Tests for Phase 13 Task 2 (doc 05 section 5): ``execute_turn``'s own real
per-turn instruction/memory injection into the router/synthesis prompt, and
the ``memory_outbox`` ``touch()`` hook fired at each of the four turn-
completion sites.

Everything here is OFFLINE, mirroring ``test_chat_orchestrator.py``'s own
discipline exactly -- this file reuses that module's public ``REGISTRY``/
``FakeDataClient``/``RecordingWriter`` (the same cross-test-module reuse
``test_entry_orchestration.py`` already does) rather than re-deriving them,
and duplicates ``_settings``/``_dev_role_client``/``_capturing_send``
locally, matching the established "each test module owns its own private
helpers" convention those two files already state explicitly.

``ProfileStore``/``MemoryStore``/``OutboxStore`` are stood in for by small
in-memory fakes below -- this codebase's established ``RecordingWriter``-
style double precedent (same public method names, plain call-recording,
never real SQL). Task 1's own three store modules already have their own
pg-backed correctness suite (``test_personalization_stores.py``); this
suite proves only that ``execute_turn`` calls the fake stores' interfaces
correctly and threads their results into the real prompt/outbox row --
never the stores' own SQL.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from poseidon.core.chat import orchestrator
from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.orchestrator import execute_turn
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.identity import DISABLED_DEFAULT_USER
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.skills.context import ConversationSlots
from tests.test_chat_orchestrator import REGISTRY, FakeDataClient, RecordingWriter

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}

# Same reference date test_chat_orchestrator.py/test_entry_orchestration.py
# already pin -- reusing their own flagship/ambiguous/entry query texts
# below depends on this exact "today".
REFERENCE_DATE = date(2026, 4, 15)

# The flagship query test_chat_orchestrator.py's own
# test_flagship_prompt_hash_matches_the_real_system_text_the_provider_saw
# already pins as producing EXACTLY two router iterations (one self-
# correction retry) -- reused verbatim here so this suite's own "fetched
# once, not once per iteration" proof rests on an independently-verified
# iteration count, not a guess.
FLAGSHIP_TEXT = "Top GP customers for Port of Singapore in April 2026"
AMBIGUOUS_TEXT = "gp for Meridiann in April 2026"
ENTRY_TEXT_EXISTING = "start an existing-customer brief"


def _settings(monkeypatch, **overrides) -> Settings:
    """Mirrors ``test_chat_orchestrator.py``'s own helper exactly."""
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LLM_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ROUTER", raising=False)
    for key, value in {**REQUIRED_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def _dev_role_client(settings: Settings) -> RoleClient:
    return RoleClient(settings, providers={"stub": DevDeterministicRouter()})


def _capturing_send():
    frames: list[str] = []

    def send(frame: str) -> None:
        frames.append(frame)

    return frames, send


def _sink(turn_id: str = "t", message_id: str = "m"):
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id=turn_id, message_id=message_id, send=send, registry=REGISTRY)
    return frames, sink


class _RecordingStub:
    """Wraps the REAL ``DevDeterministicRouter`` so these tests still
    exercise genuine routing decisions -- only ``system`` is intercepted.
    Mirrors ``test_chat_orchestrator.py``'s own identical helper (same
    class, duplicated rather than imported -- the established "each test
    module owns its own private helpers" convention)."""

    def __init__(self) -> None:
        self._inner = DevDeterministicRouter()
        self.systems: list[str] = []

    def invoke(self, *, system, messages, tools, model, params):
        self.systems.append(system)
        return self._inner.invoke(
            system=system, messages=messages, tools=tools, model=model, params=params
        )


def _run_turn(
    *,
    conversation_id: str,
    text: str,
    settings: Settings,
    state: ConversationStateStore,
    writer,
    sink: SseEnvelopeSink,
    data=None,
    role_client: RoleClient | None = None,
    profile_store=None,
    memory_store=None,
    outbox_store=None,
    client_turn_key: str | None = None,
    user=DISABLED_DEFAULT_USER,
):
    """Mirrors ``test_entry_orchestration.py``'s own ``_run`` helper, with
    the three new Phase 13 Task 2 store kwargs added -- every existing
    caller of that pattern that never passes them keeps getting ``None``
    (execute_turn's own default), which is exactly this suite's own
    "no stores wired at all" regression case below."""
    return execute_turn(
        conversation_id=conversation_id,
        user=user,
        text=text,
        client_turn_key=client_turn_key,
        settings=settings,
        registry=REGISTRY,
        data=data if data is not None else FakeDataClient(),
        state=state,
        writer=writer,
        role_client=role_client if role_client is not None else _dev_role_client(settings),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        sink=sink,
        reference_date=REFERENCE_DATE,
        profile_store=profile_store,
        memory_store=memory_store,
        outbox_store=outbox_store,
    )


# ===========================================================================
# Fake ProfileStore/MemoryStore/OutboxStore doubles -- see the module
# docstring for why these stand in for Task 1's real, pg-backed stores
# rather than re-implementing (or re-testing) any of their own SQL.
# ===========================================================================


@dataclass
class _FakeUserProfile:
    _instruction: str

    def get(self) -> dict:
        return {"system_instruction": self._instruction, "updated_at": None}


@dataclass
class _FakeProfileStore:
    instruction: str = ""
    for_user_calls: list = field(default_factory=list)

    def for_user(self, user_sub: str) -> _FakeUserProfile:
        self.for_user_calls.append(user_sub)
        return _FakeUserProfile(self.instruction)


@dataclass
class _FakeUserMemory:
    _markdown: str

    def render_markdown(self) -> str:
        return self._markdown


@dataclass
class _FakeMemoryStore:
    markdown: str = ""
    for_user_calls: list = field(default_factory=list)

    def for_user(self, user_sub: str) -> _FakeUserMemory:
        self.for_user_calls.append(user_sub)
        return _FakeUserMemory(self.markdown)


@dataclass
class _FakeOutboxStore:
    raise_on_touch: bool = False
    for_user_calls: list = field(default_factory=list)
    touch_calls: list = field(default_factory=list)

    def for_user(self, user_sub: str) -> "_FakeConversationOutbox":
        self.for_user_calls.append(user_sub)
        return _FakeConversationOutbox(user_sub, self)


@dataclass
class _FakeConversationOutbox:
    user_sub: str
    _store: _FakeOutboxStore

    def touch(self, conversation_id: str) -> None:
        self._store.touch_calls.append((self.user_sub, conversation_id))
        if self._store.raise_on_touch:
            raise RuntimeError("simulated outbox failure")


# ===========================================================================
# Real instruction/memory rendered into the actual prompt sent to the
# provider -- assert on the captured prompt TEXT, not merely "a function
# was called" (this task's own brief, verbatim).
# ===========================================================================


def test_instruction_and_memory_render_into_the_real_prompt_fetched_exactly_once(monkeypatch):
    """The strongest possible proof of this task's whole first half at
    once, mirroring ``test_chat_orchestrator.py``'s own ``test_flagship_
    prompt_hash_matches_the_real_system_text_the_provider_saw``: a
    recording stub captures the REAL ``system`` text sent to the provider,
    and the flagship query (independently pinned elsewhere as producing
    exactly two router iterations -- one self-correction retry) proves the
    fetch happens ONCE per turn, not once per iteration, and that both
    iterations see the identical rendered text.
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    stub = _RecordingStub()
    role_client = RoleClient(settings, providers={"stub": stub})
    instruction = "Always show GP in USD thousands."
    memory = "- [preference] loves concise answers (source: conv-0, at: 2026-01-01T00:00:00)"
    profile_store = _FakeProfileStore(instruction=instruction)
    memory_store = _FakeMemoryStore(markdown=memory)
    writer = RecordingWriter()
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-both",
        text=FLAGSHIP_TEXT,
        settings=settings,
        state=ConversationStateStore(),
        writer=writer,
        sink=sink,
        role_client=role_client,
        profile_store=profile_store,
        memory_store=memory_store,
    )

    assert outcome.status == "ok"
    assert len(stub.systems) == 2  # the flagship query's own pinned iteration count
    assert stub.systems[0] == stub.systems[1]  # one system per turn, reused
    for system in stub.systems:
        assert f"=== USER INSTRUCTION ===\n{instruction}" in system
        assert f"=== MEMORY ===\n{memory}" in system

    # Fetched exactly ONCE per turn -- not once per the two router
    # iterations above, and not once per render (this function AND
    # _router_prompt_provenance both render the system prompt).
    assert profile_store.for_user_calls == [DISABLED_DEFAULT_USER.sub]
    assert memory_store.for_user_calls == [DISABLED_DEFAULT_USER.sub]

    # The dedicated provenance-duplicate-render assertion this task's brief
    # calls out by name: _router_prompt_provenance's own independent render
    # must hash to the SAME text the provider actually received, now that
    # instruction/memory are real, non-empty values (not the old
    # permanently-empty module constants) -- a mismatch here would
    # silently break Phase 11's replay/reconciliation auditing.
    real_hash = hashlib.sha256(stub.systems[0].encode("utf-8")).hexdigest()
    assert writer.append_llm_calls[0]["prompt_hash"] == real_hash
    assert writer.append_llm_calls[1]["prompt_hash"] == real_hash


def test_a_user_with_neither_instruction_nor_memory_gets_a_prompt_with_neither_section(
    monkeypatch,
):
    """Regression: ``assemble_system``'s "empty is empty" rule (Phase 5/6)
    must survive unchanged for a brand-new user whose ``ProfileStore``/
    ``MemoryStore`` both genuinely have nothing yet (Task 1's own
    documented default shape -- ``get()["system_instruction"] == ""``,
    ``render_markdown() == ""``) -- real stores ARE wired here, unlike the
    ``None``-store degrade case below, so this proves the new fetch path
    itself preserves "empty means no section," not merely that skipping the
    fetch entirely does.
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    stub = _RecordingStub()
    role_client = RoleClient(settings, providers={"stub": stub})
    profile_store = _FakeProfileStore(instruction="")
    memory_store = _FakeMemoryStore(markdown="")
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-neither",
        text=FLAGSHIP_TEXT,
        settings=settings,
        state=ConversationStateStore(),
        writer=RecordingWriter(),
        sink=sink,
        role_client=role_client,
        profile_store=profile_store,
        memory_store=memory_store,
    )

    assert outcome.status == "ok"
    assert stub.systems
    for system in stub.systems:
        assert "=== USER INSTRUCTION ===" not in system
        assert "=== MEMORY ===" not in system


def test_no_stores_wired_at_all_still_gets_a_prompt_with_neither_section(monkeypatch):
    """The ``profile_store=None``/``memory_store=None`` degrade path --
    ``execute_turn``'s own default for every pre-Task-2 caller (e.g. every
    test in ``test_chat_orchestrator.py``, none of which pass these two new
    kwargs) -- must produce the IDENTICAL "no section at all" prompt Phase
    6's old permanently-empty ``_USER_INSTRUCTION``/``_MEMORY_DOC`` module
    constants always did. This is what makes deleting those two constants
    outright a safe, behavior-preserving change for every caller that has
    not adopted the new stores yet (today: ``api/live_chat.py``'s own
    ``execute_turn`` call)."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    stub = _RecordingStub()
    role_client = RoleClient(settings, providers={"stub": stub})
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-no-stores",
        text=FLAGSHIP_TEXT,
        settings=settings,
        state=ConversationStateStore(),
        writer=RecordingWriter(),
        sink=sink,
        role_client=role_client,
    )

    assert outcome.status == "ok"
    assert stub.systems
    for system in stub.systems:
        assert "=== USER INSTRUCTION ===" not in system
        assert "=== MEMORY ===" not in system


# ===========================================================================
# The outbox touch() hook -- one call per completed turn, at each of the
# four turn-completion sites named by this task's brief. mock/spy the
# store; assert call count and argument, never the store's own internals.
# ===========================================================================


def test_outbox_touched_once_after_a_normal_ok_turn_the_router_dispatches(monkeypatch):
    """Site 1: ``execute_turn``'s own main body, the ``ok`` path after a
    real router dispatch."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    outbox_store = _FakeOutboxStore()
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-touch-ok",
        text=FLAGSHIP_TEXT,
        settings=settings,
        state=ConversationStateStore(),
        writer=RecordingWriter(),
        sink=sink,
        outbox_store=outbox_store,
    )

    assert outcome.status == "ok"
    assert outbox_store.for_user_calls == [DISABLED_DEFAULT_USER.sub]
    assert outbox_store.touch_calls == [(DISABLED_DEFAULT_USER.sub, "conv-touch-ok")]


def test_outbox_touched_once_after_a_clarify_turn_from_an_ambiguous_customer(monkeypatch):
    """Site 2: ``_finish_clarify`` (the ``parse_turn``-driven clarify
    short-circuit)."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    outbox_store = _FakeOutboxStore()
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-touch-clarify",
        text=AMBIGUOUS_TEXT,
        settings=settings,
        state=ConversationStateStore(),
        writer=RecordingWriter(),
        sink=sink,
        outbox_store=outbox_store,
    )

    assert outcome.status == "clarify"
    assert outbox_store.for_user_calls == [DISABLED_DEFAULT_USER.sub]
    assert outbox_store.touch_calls == [(DISABLED_DEFAULT_USER.sub, "conv-touch-clarify")]


def test_outbox_touched_once_after_a_d19_entry_turn(monkeypatch):
    """Site 3: ``_finish_entry`` (D19's bubble-entry short-circuit, before
    ``parse_turn`` ever runs)."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    outbox_store = _FakeOutboxStore()
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-touch-entry",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=ConversationStateStore(),
        writer=RecordingWriter(),
        sink=sink,
        outbox_store=outbox_store,
    )

    assert outcome.status == "clarify"
    assert outbox_store.for_user_calls == [DISABLED_DEFAULT_USER.sub]
    assert outbox_store.touch_calls == [(DISABLED_DEFAULT_USER.sub, "conv-touch-entry")]


def test_outbox_touched_once_after_a_d19_subject_turn_dispatch(monkeypatch):
    """Site 4: ``_finish_subject_turn`` (D19's subject-turn deterministic
    brief dispatch, ``ok`` path) -- prospect mode, which dispatches on the
    raw subject text with no customer-resolution data-client round trip
    (probe-verified directly against this exact scenario before pinning:
    ``FakeDataClient`` reached zero times)."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    outbox_store = _FakeOutboxStore()
    state = ConversationStateStore()
    state.put("conv-touch-subject", ConversationSlots(mode="new_prospect"))
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-touch-subject",
        text="Meridian Global Shipping",
        settings=settings,
        state=state,
        writer=RecordingWriter(),
        sink=sink,
        outbox_store=outbox_store,
    )

    assert outcome.status == "ok"
    assert outbox_store.for_user_calls == [DISABLED_DEFAULT_USER.sub]
    assert outbox_store.touch_calls == [(DISABLED_DEFAULT_USER.sub, "conv-touch-subject")]


def test_outbox_touch_failure_is_swallowed_and_does_not_fail_the_turn(monkeypatch, caplog):
    """Mirrors ``RunLogWriter``'s own established "never raises the turn"
    contract (``core/runlog.py``'s module docstring) -- this task's own new
    ``_touch_outbox`` must uphold the identical contract, not merely
    inherit it by accident. Logged at WARNING (not ERROR, unlike
    ``RunLogWriter``'s own writes) per this task's own brief."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    outbox_store = _FakeOutboxStore(raise_on_touch=True)
    _, sink = _sink()

    with caplog.at_level(logging.WARNING, logger="poseidon.core.chat.orchestrator"):
        outcome = _run_turn(
            conversation_id="conv-touch-fails",
            text=ENTRY_TEXT_EXISTING,
            settings=settings,
            state=ConversationStateStore(),
            writer=RecordingWriter(),
            sink=sink,
            outbox_store=outbox_store,
        )

    # The turn completes exactly as it would have with no outbox_store at
    # all -- a touch() failure is invisible to the turn's own outcome.
    assert outcome.status == "clarify"
    assert outbox_store.touch_calls == [(DISABLED_DEFAULT_USER.sub, "conv-touch-fails")]

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "outbox touch failed" in r.getMessage() and "conv-touch-fails" in r.getMessage()
        for r in warnings
    )
    # Never escalated to ERROR -- this task's own brief pins WARNING.
    assert not any(r.levelno > logging.WARNING for r in caplog.records)


def test_outbox_not_touched_when_no_store_is_wired(monkeypatch):
    """``outbox_store=None`` (every pre-Task-2 caller) is a silent no-op --
    proven directly here rather than only inferred from the rest of
    ``test_chat_orchestrator.py``/``test_entry_orchestration.py`` staying
    green."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    _, sink = _sink()

    outcome = _run_turn(
        conversation_id="conv-touch-none",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=ConversationStateStore(),
        writer=RecordingWriter(),
        sink=sink,
    )

    assert outcome.status == "clarify"  # no crash from a missing outbox_store


def test_orchestrator_personalization_module_files_are_ascii_on_disk():
    """Mirrors ``test_chat_orchestrator.py``'s own identically-named check
    exactly -- this codebase's ASCII-on-disk convention, applied to this
    new file and the orchestrator module it tests."""
    paths = (Path(orchestrator.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"

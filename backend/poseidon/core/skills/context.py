"""What a skill is handed when it runs: its seams and its conversation.

:class:`SkillContext` is deliberately small. Doc 02 §3 describes a fuller
context (``llm``, ``tools``, ``user``, ``profile``, ``run``); each of those
fields arrives with the phase that owns it rather than as a ``None``-typed
placeholder, because a placeholder invites code that pretends the capability
exists. ``tools`` arrived in Phase 7 Task 1; ``llm`` and ``emit_part`` arrive
in Phase 8 Task 1; ``user`` arrives in Phase 9 Task 1 (all four named below);
``profile`` and ``run`` remain pending their own owning phases. Today a
skill gets exactly ten things (``report_store`` and ``prompt_tree`` arrive
in Phase 17 PR A1):

``data``      the adapter seam of doc 04 — a :class:`DataClient`, never a
              connection, a cursor or SQL.
``artifacts`` the object store, or ``None`` for the skills that produce no
              file (``data_qa.metric_query`` passes ``None``).
``settings``  the validated environment contract.
``state``     the conversation slots the deterministic parsing pipeline
              (doc 02 §5, phase 4) fills in, carried across turns.
``tools``     external tool servers (doc 02 §7) behind
              :class:`~poseidon.mcp.registry.ToolServerRegistry` — typed
              ``object``, never the registry's own protocol, so this module
              needs no ``poseidon.mcp`` import at all (not even under
              ``TYPE_CHECKING``): the dependency runs one way,
              ``poseidon.mcp`` on ``poseidon.core``, never back. A skill
              reaching for one casts through the typed interface at its own
              call site. Defaulted to ``None`` so every existing
              ``SkillContext(...)`` call site (P3/P4/P6) keeps working
              unchanged; real until Phase 7 Task 4 wires a registry through
              ``api/app.py``. (``poseidon.mcp`` lives inside the
              ``poseidon`` package itself, by amendment — see
              ``poseidon/mcp/__init__.py``'s docstring.)
``llm``       the role-based LLM seam a subskill calls for live-mode
              synthesis -- :class:`~poseidon.core.llm.roles.RoleClient`,
              typed ``object`` for the same reason ``tools`` is: keeping the
              dependency direction one-way. ``poseidon.core.llm.loop``
              already imports THIS module (a dispatch needs
              ``SkillContext``'s type); a real ``RoleClient`` import here
              would run the edge back the other way, entangling the two
              packages even though no single pair of modules cycles today --
              exactly the "no real cycle, only a direction worth keeping
              one-way" case ``tools`` itself documents below. A skill calls
              ``ctx.llm.invoke(role=..., system=..., messages=...)`` and
              casts through the typed interface at its own call site, same
              as ``tools``. Defaulted to ``None`` so every pre-Phase-8 call
              site keeps working unchanged; wired for real by
              ``core/chat/orchestrator.py`` starting Phase 8 Task 1.
``emit_part`` the progressive-display seam: an optional callable,
              ``(part: dict) -> None``, that a subskill invokes once per
              completed phase to stream a part to the user immediately,
              rather than making the whole dispatch wait for its own
              ``tool_done``. Typed ``object`` rather than
              ``Callable[[dict], None]`` for uniformity with every other
              seam this class carries, not because ``Callable`` would cycle
              -- a skill only ever calls it positionally, never inspects it
              as anything but callable. ``None`` is not merely the default,
              it is a state real callers produce on purpose: any call site
              that wires no live sink (every hand-built ``SkillContext`` in
              this codebase's own unit tests, ``api/dev_runner.py``'s
              dev-only skill runner) leaves it unset, so a skill that streams
              progressively must guard ``if ctx.emit_part is not None``
              before calling it rather than assume every dispatch has one.
              See ``core/chat/events.py``'s ``SseEnvelopeSink.part_emitter``
              for what a real caller wires it to, and
              ``core/chat/orchestrator.py`` for where that wiring happens.
``user``      the resolved caller identity (Phase 9's ``IdentityProvider``
              seam, doc 05 section 2) -- a
              :class:`~poseidon.core.identity.UserContext`, typed ``object``
              for the SAME reason ``tools``/``llm`` are: this class stays
              free of importing ``core.identity`` even though, unlike those
              two, no real import cycle would result -- consistency with
              every other non-``data``/``settings``/``state`` seam on this
              class beats making ``user`` the one exception typed
              concretely. Defaulted to ``None`` so every pre-Phase-9
              ``SkillContext(...)`` call site (this codebase's own test
              suite builds many) keeps working unchanged; wired for real by
              ``core/chat/orchestrator.py`` starting this task, which reads
              the caller's own already-resolved ``UserContext`` and passes
              it through verbatim -- never examined here any more than
              ``tools``/``llm`` are. No skill reads ``ctx.user`` yet; a
              future one casts through the typed interface at its own call
              site, same as ``tools``.
``report_store`` the ``report_run`` store (Phase 17 PR A1) -- a
              :class:`~poseidon.reports.store.ReportStore`, typed ``object``
              for the same one-way-dependency reason as ``tools``/``llm``.
              ``None`` is a real state: every call site that cannot produce a
              report leaves it unset, and ``reporting.monthly_performance``
              answers a structured 500 when it is missing.
``prompt_tree`` the D46 prompt tree (Phase 17 PR A1) -- a
              :class:`~poseidon.core.llm.prompt_tree.PromptTree`, typed
              ``object`` for the same reason. ``None`` is a real state: a
              call site that wires no tree leaves it unset, and the reporting
              skill falls back to the default ``PromptTree()``.

``artifacts`` is annotated as a string under a ``TYPE_CHECKING`` guard on
purpose, and permanently so: :mod:`poseidon.core.artifacts` (Task 4) imports
``ArtifactRef`` from this very module, so a real runtime import here would be
circular. Dataclasses never evaluate their annotations, so the forward
reference costs nothing at runtime. ``tools``, ``llm`` and ``emit_part`` take
the plainer route of typing themselves ``object`` instead, because unlike
``ArtifactRef`` there is no real cycle to dodge for any of the three -- only a
dependency DIRECTION worth keeping one-way on purpose.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from poseidon.core.config import Settings
from poseidon.core.data.client import DataClient

if TYPE_CHECKING:  # avoids the circular import — see the docstring above
    from poseidon.core.artifacts import ArtifactStore


@dataclass(frozen=True)
class ConversationSlots:
    """The carried-over subject of the conversation (doc 02 §5).

    Slot carry semantics belong to the parsing pipeline, not here: omitted
    slot carries the previous value, an explicit empty clears it, and a new
    value replaces (never merges). This dataclass is the frozen snapshot that
    the parsing pipeline hands to a skill.

    ``period_a``/``period_b`` are first-of-period dates — the resolved
    ``{period_a, period_b}`` pair of the period parser, not a rendered
    window.

    ``region``/``topic``/``pass_through`` were added in Phase 4 Task 1
    (additive growth only, per the P3 final-review rule: never reshape).
    ``pass_through`` is the cross-turn exact-value pass-through of doc 02
    §5 — ``(label, exact_value)`` pairs a skill most recently returned, so
    the router can inject certified values into its next call instead of
    re-deriving them from prose history. Like every other slot it replaces
    wholesale on a new turn; it never merges or accumulates across turns.

    ``period_a_end``/``period_b_end`` (Phase 17 PR 0, D62) are the EXCLUSIVE
    ends of the windows whose first days ``period_a``/``period_b`` hold, so a
    carried year stays a year. They are appended after ``pass_through`` --
    additive growth only -- and default to ``None``: a slot with a start and
    no end is read as the month containing the start by
    ``parsing.period_labels.slot_window``, which is the ONLY place that
    fallback lives. Nothing here or in the serializer invents an end.
    """

    customer: str | None = None
    port: str | None = None
    period_a: date | None = None
    period_b: date | None = None
    mode: str = "default"
    region: str | None = None
    topic: str | None = None
    pass_through: tuple[tuple[str, str], ...] = ()
    period_a_end: date | None = None
    period_b_end: date | None = None
    perspective: str | None = None  # customer | supplier | book
    office_column: str | None = None  # CUSTOMER_TEAM_NAME | PRIMARY_SUPPLY_TEAM_OFFICE
    office_value: str | None = None
    report_ref: str | None = None  # report_run.id of the last report in this conversation


@dataclass(frozen=True)
class ArtifactRef:
    """A file a skill produced, addressable by the frontend's artifact part.

    ``url`` is a presigned GET: the browser fetches the object directly and
    the backend never proxies bytes.
    """

    name: str
    url: str
    mime: str


@dataclass(frozen=True)
class SkillContext:
    data: DataClient
    artifacts: "ArtifactStore | None"
    settings: Settings
    state: ConversationSlots = ConversationSlots()
    tools: object | None = None
    llm: object | None = None
    emit_part: object | None = None
    user: object | None = None
    report_store: object | None = None
    prompt_tree: object | None = None
    # the chat conversation a dispatch belongs to; the report flow sets it
    conversation_id: str | None = None

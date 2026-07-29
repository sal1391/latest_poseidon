"""What a skill is handed when it runs: its seams and its conversation.

:class:`SkillContext` is deliberately small. Doc 02 §3 describes a fuller
context (``llm``, ``tools``, ``user``, ``profile``, ``run``); each of those
fields arrives with the phase that owns it rather than as a ``None``-typed
placeholder, because a placeholder invites code that pretends the capability
exists. Today a skill gets exactly four things:

``data``      the adapter seam of doc 04 — a :class:`DataClient`, never a
              connection, a cursor or SQL.
``artifacts`` the object store, or ``None`` for the skills that produce no
              file (``data_qa.metric_query`` passes ``None``).
``settings``  the validated environment contract.
``state``     the conversation slots the deterministic parsing pipeline
              (doc 02 §5, phase 4) fills in, carried across turns.

``artifacts`` is annotated as a string under a ``TYPE_CHECKING`` guard on
purpose, and permanently so: :mod:`poseidon.core.artifacts` (Task 4) imports
``ArtifactRef`` from this very module, so a real runtime import here would be
circular. Dataclasses never evaluate their annotations, so the forward
reference costs nothing at runtime.
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
    """

    customer: str | None = None
    port: str | None = None
    period_a: date | None = None
    period_b: date | None = None
    mode: str = "default"
    region: str | None = None
    topic: str | None = None
    pass_through: tuple[tuple[str, str], ...] = ()


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

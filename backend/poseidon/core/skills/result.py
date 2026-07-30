"""What a skill hands back: typed message parts, provenance, and failures.

A part is a plain ``{"kind": str, "payload": dict}`` dict whose ``kind``
comes verbatim from the frontend contract (doc 01 §4). The backend has no
renderer and no opinion about presentation — it only promises the shape. The
constructors below exist so that shape is written once: a skill that builds
part dicts by hand is how ``columns``/``headers`` drift apart between two
skills that render into the same table component.

Failures are RFC-7807 problem details (``type``/``title``/``detail``/
``status``). Skills return them inside ``SkillResult.error`` with
``ok=False``; they do not raise for business failures, and the API layer maps
the same dict onto an HTTP problem response.
"""

from dataclasses import dataclass, field
from typing import Any

from .context import ArtifactRef


def text_part(markdown: str) -> dict:
    """Prose. Streamed as markdown by the frontend."""
    return {"kind": "text", "payload": {"markdown": markdown}}


def table_part(columns: list[str], rows: list[list]) -> dict:
    """A tabular result — a breakdown, a top-N, a metric/value pair list.

    ``columns`` and ``rows`` are copied rather than referenced: a part is a
    value that has already been emitted, and a caller that keeps mutating the
    list it passed in must not be able to rewrite it after the fact.
    """
    return {
        "kind": "table",
        "payload": {"columns": list(columns), "rows": [list(row) for row in rows]},
    }


def metric_grid_part(periods: dict, metrics: list[dict]) -> dict:
    """The side-by-side metric card grid.

    ``periods`` maps the two window labels to their descriptors
    (``{"a": {...}, "b": {...}}``) and each entry of ``metrics`` carries
    ``{"name", "friendly", "a", "b", "unit"}``.
    """
    return {
        "kind": "metric_grid",
        "payload": {"periods": dict(periods), "metrics": [dict(m) for m in metrics]},
    }


def phase_section_part(title: str, markdown: str) -> dict:
    """A titled block of markdown -- one phase of a multi-phase skill (a
    brief) rendering as its own section rather than one undifferentiated
    wall of prose (Poseidon Phase 8, doc 02 section 4).

    Introduced by the ``research`` subskill (Phase 8 Task 2, in its own
    ``tools/format_phase_section.py`` -- Task 2's own sanctioned edit
    surface did not include this file). Relocated HERE in Phase 8 Task 3,
    once a second call site existed (the ``contextualize``/``strategize``
    subskills, each contributing one ``phase_section`` of their own) --
    exactly the promotion Task 2's own report named as a future call for
    "whichever task first needs phase_section from a second call site".
    ``research``'s own module keeps working unchanged: it re-imports this
    function rather than defining it, so ``format_success``/
    ``format_degraded`` there call the SAME object, not a copy.
    """
    return {"kind": "phase_section", "payload": {"title": title, "markdown": markdown}}


def problem(status: int, title: str, detail: str, type_: str = "about:blank") -> dict:
    """An RFC-7807 problem detail.

    ``type_`` defaults to ``about:blank``, which RFC 7807 defines as "the
    status code is the whole story" — the honest default until a phase
    introduces a URI namespace for its failure modes.
    """
    return {"type": type_, "title": title, "detail": detail, "status": status}


@dataclass(frozen=True)
class SkillResult:
    """The single return type of every skill.

    Frozen so a caller cannot swap ``ok`` out from under the parts that
    justify it. ``error`` carries a :func:`problem` dict whenever ``ok`` is
    False.

    Doc 02 §3's fuller shape also carries ``usage: TokenUsage``; it arrives
    with the first LLM-calling phase (5), since nothing in this codebase
    spends a token yet and a field that is always zero teaches a reader the
    wrong thing about what has been measured.
    """

    ok: bool
    parts: list[dict] = field(default_factory=list)
    proof: list[str] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    error: dict[str, Any] | None = None

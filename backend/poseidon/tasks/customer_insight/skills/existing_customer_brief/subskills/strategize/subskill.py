"""The ``strategize`` subskill: one Sonnet-tier synthesis call that fills
the exact Salesforce CRM field template from the contextualizer's own
narrative, the research subskill's own outputs, and the internal data
summary (Phase 8 Task 3; doc 02 section 4's "Subskill strategize -- Sonnet
fills the exact Salesforce CRM field template ... consuming 2 + 3 + the
internal data summary").

``run(ctx, mode, subject, context_text, research_inputs, data_summary) ->
SubskillResult`` (the SAME frozen dataclass ``research.subskill`` defines
and ``contextualize.subskill`` also reuses -- see that module's own
docstring for why). Branches on ``ctx.settings.llm_mode`` exactly once,
same as ``contextualize``:

- ``"stub"``: never touches ``ctx.llm``. Opens with the SAME byte-pinned
  line every stub-mode subskill this phase ships opens with, then fills
  every one of the seven CRM headers below deterministically: "Account
  Name" from ``subject`` (always known); "Current Services" from
  ``data_summary`` (see "CURRENT SERVICES" below); every other header
  (Industry, Opportunity Summary, Key Contacts Strategy, Risk Factors,
  Next Steps) gets the pinned placeholder "[requires live synthesis]" --
  these five genuinely need model reasoning over the account context and
  research this subskill received, which a template cannot honestly fake.
- ``"live"``: renders ``prompts/strategist.md`` (the CRM template itself
  lives IN that file -- see its own OUTPUT FORMAT section) and calls
  ``ctx.llm.invoke(role="synthesis", ...)``. Same error / ``ctx.llm is
  None`` handling as ``contextualize`` (see that module's docstring for
  the full reasoning, not repeated here).

CURRENT SERVICES (disclosed judgment call). The brief's stub contract
names two things as deterministic: "subject" (Account Name) and "services
from data" (Current Services) -- everything else is the placeholder.
``data_summary`` is a plain mapping this subskill does not otherwise
prescribe a shape for (matching ``contextualize.subskill``'s own
``data_block`` "schema-agnostic mapping" contract), except for ONE
convention this module itself establishes: an optional ``"services"`` key
holding a sequence of service-name strings. When ``mode`` is
``MODE_PROSPECT``, the field is always the pinned "Prospect -- no current
services" text (a prospect KNOWS it has none -- doc 02's own
new_prospect_brief bullet 4; this is a known fact, not a synthesis gap,
so it is never the "[requires live synthesis]" placeholder). Otherwise, a
non-empty ``services`` list renders as its comma-joined values (real
data); an EXISTING customer with no ``"services"`` key is a genuine gap
this subskill cannot honestly fill from data alone, so it falls back to
"[requires live synthesis]" too -- distinct from a prospect's pinned text
because the two are different FACTS (one is "definitely none", the other
is "unknown from what this subskill was handed").

PROMPT LOADING: see ``contextualize.subskill``'s own module docstring for
the shared "PROMPT LOADING" reasoning (identical here, independently
declared per this codebase's own near-identical-siblings convention --
see e.g. ``research.subskill``'s own module docstring, "SUBSKILL-SHARING
SEAM").

THE CRM HEADERS CONSTANT. ``_CRM_HEADERS`` is the single source of truth
this module's stub path renders from; the SAME seven strings are typed
directly into ``prompts/strategist.md`` as prose (doc 03 section 4's own
"the strategist prompt must contain the exact CRM headers" contract) --
the two are cross-checked by
``test_strategist_render_contains_every_crm_header_byte_exact`` in
``test_brief_subskills.py`` rather than sharing one code path, since one
is Jinja-rendered markdown prose meant for a model to read and the other
is a Python tuple driving an f-string loop; forcing them through one
function would not remove any real duplication, only obscure two
genuinely different renderers behind one.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import jinja2

from poseidon.core.llm.roles import RoleClient
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import phase_section_part

from ..research.subskill import MODE_EXISTING, MODE_PROSPECT, SubskillResult

# See contextualize/subskill.py's own identical comment -- same three-hop
# shape, this subskill's own sibling directory.
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
_PROMPT_NAME = "strategist.md"

_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_PROMPTS_DIR)),
    undefined=jinja2.StrictUndefined,
    autoescape=False,
    auto_reload=False,
)

_SYNTHESIS_ROLE = "synthesis"
_TITLE = "Strategy"

_EM_DASH = chr(0x2014)

_STUB_OPENING = "Stub-mode synthesis " + _EM_DASH + " flip LLM_MODE=live for model narrative."

_FAILURE_TEXT = (
    "Strategy synthesis is unavailable right now "
    + _EM_DASH
    + " the synthesis model returned an error."
)

_USER_DIRECTIVE = "Fill in the CRM fields now, following the instructions above."

_REQUIRES_LIVE = "[requires live synthesis]"

# The exact Salesforce CRM field template's headers -- author them (Task 3
# brief); also typed directly into prompts/strategist.md as prose (see
# the module docstring's own "THE CRM HEADERS CONSTANT" section for why
# the two are cross-checked by a test rather than shared by one function).
_CRM_HEADERS: tuple[str, ...] = (
    "Account Name",
    "Industry",
    "Current Services",
    "Opportunity Summary",
    "Key Contacts Strategy",
    "Risk Factors",
    "Next Steps",
)

# The pinned rule for a new prospect's "Current Services" field -- doc 02's
# own "Prospect -- no current services" rule (D10's own new_prospect_brief
# bullet), byte-identical to the phrase typed into prompts/strategist.md's
# own mode-conditional block (that file may carry the real em dash
# literally -- P5 precedent for prompt .md files; this .py file cannot, so
# it is built from _EM_DASH like every other pinned message in this
# codebase).
_PROSPECT_NO_SERVICES = "Prospect " + _EM_DASH + " no current services"


def render_prompt(**context: object) -> str:
    """Render ``prompts/strategist.md`` -- see
    ``contextualize.subskill.render_prompt`` for the full rationale
    (identical here)."""
    return _ENV.get_template(_PROMPT_NAME).render(**context)


def _format_mapping(mapping: Mapping[str, object]) -> str:
    """See ``contextualize.subskill._format_mapping`` (identical logic,
    independently declared -- this module's own copy, applied to
    ``data_summary`` rather than ``data_block``)."""
    if not mapping:
        return "(none on file)"
    return "\n".join(f"- {key}: {value}" for key, value in mapping.items())


def _format_research_block(research_inputs: tuple[dict, ...]) -> str:
    """See ``contextualize.subskill._format_research_block`` (identical
    logic, independently declared)."""
    if not research_inputs:
        return "(no research available)"
    lines = []
    for item in research_inputs:
        if item.get("degraded"):
            reason = item.get("degrade_reason") or "unknown error"
            lines.append(f"{item['title']}: unavailable ({reason})")
        else:
            lines.append(f"{item['title']}: {item.get('summary', '')}")
    return "\n".join(lines)


def _current_services(mode: str, data_summary: Mapping[str, object]) -> str:
    """See the module docstring's "CURRENT SERVICES" section for the full
    three-way rule this implements."""
    if mode == MODE_PROSPECT:
        return _PROSPECT_NO_SERVICES
    services = cast(Sequence[str], data_summary.get("services") or ())
    if services:
        return ", ".join(services)
    return _REQUIRES_LIVE


def _stub_text(subject: str, mode: str, data_summary: Mapping[str, object]) -> str:
    fields = {
        "Account Name": subject,
        "Industry": _REQUIRES_LIVE,
        "Current Services": _current_services(mode, data_summary),
        "Opportunity Summary": _REQUIRES_LIVE,
        "Key Contacts Strategy": _REQUIRES_LIVE,
        "Risk Factors": _REQUIRES_LIVE,
        "Next Steps": _REQUIRES_LIVE,
    }
    body = "\n".join(f"{header}: {fields[header]}" for header in _CRM_HEADERS)
    return _STUB_OPENING + "\n\n" + body


def run(
    ctx: SkillContext,
    mode: str,
    subject: str,
    context_text: str,
    research_inputs: tuple[dict, ...],
    data_summary: Mapping[str, object],
) -> SubskillResult:
    """See the module docstring for the full stub/live/failure contract.

    Raises ``ValueError`` for a ``mode`` outside ``{MODE_EXISTING,
    MODE_PROSPECT}`` -- see ``contextualize.subskill.run``'s identical
    precedent and reasoning.
    """
    if mode not in (MODE_EXISTING, MODE_PROSPECT):
        raise ValueError(
            f"strategize subskill: unknown mode {mode!r} -- expected "
            f"one of {sorted((MODE_EXISTING, MODE_PROSPECT))}"
        )

    if ctx.settings.llm_mode == "stub":
        text = _stub_text(subject, mode, data_summary)
        failed = False
    elif ctx.llm is None:
        text = _FAILURE_TEXT
        failed = True
    else:
        role_client = cast(RoleClient, ctx.llm)
        system = render_prompt(
            subject=subject,
            mode=mode,
            context_text=context_text,
            research_block=_format_research_block(research_inputs),
            data_summary_block=_format_mapping(data_summary),
        )
        response = role_client.invoke(
            role=_SYNTHESIS_ROLE,
            system=system,
            messages=[{"role": "user", "content": _USER_DIRECTIVE}],
            tools=[],
        )
        if response.stop_reason == "error":
            text = _FAILURE_TEXT
            failed = True
        else:
            text = response.text
            failed = False

    part = phase_section_part(_TITLE, text)
    return SubskillResult(parts=(part,), synthesis_inputs=({"text": text},), failed=failed)


def failed_result() -> SubskillResult:
    """See ``contextualize.subskill.failed_result``'s own docstring for the
    full rationale (identical here, independently declared per this
    codebase's own near-identical-siblings convention): skill.py's
    exception-escape guard (P8 whole-branch final-review wave, 2026-07-30,
    item 2 / I-4) synthesizes this when a call to :func:`run` raises
    outright, reusing the SAME pinned failure text :func:`run` itself
    already returns for its own internal error case."""
    part = phase_section_part(_TITLE, _FAILURE_TEXT)
    return SubskillResult(parts=(part,), synthesis_inputs=({"text": _FAILURE_TEXT},), failed=True)

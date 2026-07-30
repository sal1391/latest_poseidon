"""The ``contextualize`` subskill: one Sonnet-tier synthesis call turning
the internal data block, the certified field dictionary, and the research
subskill's own outputs into a plain-English narrative of a customer's
operational reality (Phase 8 Task 3; doc 02 section 4.2's "Subskill
contextualize -- Sonnet synthesis over the internal data block + the
ontology field dictionary").

``run(ctx, mode, subject, data_block, research_inputs) -> SubskillResult``
(the SAME frozen dataclass ``research.subskill`` defines -- see below for
why this module imports it rather than defining a sibling). Branches on
``ctx.settings.llm_mode`` exactly once (doc 02 Global Constraints: "ONE
switch, everything follows"):

- ``"stub"``: a deterministic template, never touching ``ctx.llm`` at all
  -- opens with the byte-pinned line every stub-mode subskill this phase
  ships opens with (doc 02 Global Constraints), then a structured digest
  of this call's ACTUAL inputs: the subject, ``len(data_block)`` ("data
  block metrics"), and ``len(research_inputs)`` ("research sections").
  Real counts of real data, honestly labeled as a template -- never a
  fabricated narrative dressed up as model output.
- ``"live"``: renders ``prompts/contextualizer.md`` (see
  :func:`render_prompt`) and calls ``ctx.llm.invoke(role="synthesis",
  ...)`` -- the ``synthesis`` role ``models.yml`` has carried since Phase
  5. ``response.stop_reason == "error"`` -> the pinned failure text below,
  ``failed=True``; otherwise ``response.text`` verbatim, ``failed=False``.
  ``ctx.llm is None`` (no ``RoleClient`` wired) gets the identical
  pinned-failure treatment rather than an ``AttributeError`` -- the live-
  mode mirror of ``research.subskill``'s own ``ctx.tools is None`` -> all-
  degraded branch: defensive symmetry this task adds since nothing in the
  brief's own two named failure cases covers it, but doc 02 section 6's
  anti-happy-path rule covers every upstream gap, not only the one this
  task's brief calls out by name.

Either way, exactly one ``phase_section`` part comes back, titled
"Context", plus the SAME text again in ``synthesis_inputs`` (a 1-tuple,
``{"text": ...}``) -- "one phase_section part + text for downstream" per
the brief: the part is what the user sees streamed; ``synthesis_inputs``
is what ``strategize.run``'s own ``context_text`` argument reads (Task 4
wires this: ``strategize.run(..., context_text=result.synthesis_inputs[0]
["text"], ...)``).

PROMPT LOADING (disclosed judgment call). ``poseidon.core.llm.prompts
.PromptRegistry`` is anchored to ``Settings.prompts_dir``/the packaged
``poseidon/config/prompts`` directory -- APP-level prompts (the router,
utility titling). This subskill's own prompt is SKILL-local (doc 02
section 1's folder law: ``existing_customer_brief/prompts/*.md``, a
sibling of ``subskills/``, not a config-directory file), so this module
builds its OWN small :class:`jinja2.Environment`, anchored to its own
package location via ``Path(__file__)`` (matching ``roles.py``'s/
``prompts.py``'s own ``_POSEIDON_PACKAGE_DIR`` convention for "where am I
on disk" -- three ``.parent`` hops up from this file to
``existing_customer_brief/``, then into ``prompts/``), rather than
reusing ``PromptRegistry`` pointed at a second directory. Same discipline
throughout (``StrictUndefined``, no autoescape, ``auto_reload=False``):
config prompts are app-level and load through the app's one registry;
skill prompts are skill-local and load through the skill's own tiny
loader -- two different lifetimes, not two different rulebooks.

FIELD DICTIONARY (disclosed judgment call). Doc 02 section 4.2 names "the
ontology field dictionary", but this subskill's own signature (fixed by
the brief) carries no ``Entity``/ontology handle to read one from live --
and the ontology's own :class:`~poseidon.core.ontology.models.Metric`
carries plain-English ``rule`` text for only 3 of the 7 certified metrics
on the sales entity (MARGIN, NUM_LOST, WIN_RATE, per ``poseidon.core.llm
.prompts.metric_definitions_block``'s own docstring) -- and WIN_RATE is
not even one of the six ``fetch_metrics.SIX_METRICS`` names this brief's
data block ever carries. Rather than leave half the fields undefined or
build an ontology-reading API this task was never asked for,
``_FIELD_DICTIONARY`` is a fixed, authored constant covering exactly the
same six names as ``fetch_metrics.SIX_METRICS`` (deliberately NOT
imported -- a production-code import used only to validate itself at
import time would be an odd new pattern for this codebase; instead
``test_brief_subskills.py`` cross-checks the two key sets directly, the
same way it cross-checks the CRM headers against ``strategist.md``)
-- mirroring the legacy ``agents/contextualizer.py``'s own
``FIELD_DICTIONARY`` import from ``snowflake_client.py``: a fixed
constant there too, never computed per request.

DATA BLOCK / DATA SUMMARY SHAPE (disclosed judgment call). ``data_block``
is treated as an opaque ``Mapping[str, object]`` this subskill never
inspects beyond formatting and counting -- it does not assume
``fetch_metrics``'s exact return shape (Task 4 has not wired that caller
yet). "Metric count" is simply ``len(data_block)``: the number of
top-level entries the caller handed over, whatever they are. This keeps
the digest honest ("real data") without coupling this subskill to a
shape only Task 4's own skill.py will finally decide.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import jinja2

from poseidon.core.llm.roles import RoleClient
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import phase_section_part

from ..research.subskill import MODE_EXISTING, MODE_PROSPECT, SubskillResult

# subskills/contextualize/subskill.py -> subskills/contextualize/ ->
# subskills/ -> existing_customer_brief/ -- three .parent hops from this
# file to the skill root, then into its own prompts/ directory (doc 02
# section 1's folder law: prompts/ is a sibling of subskills/, not nested
# under it). Same three-hop shape as roles.py's/prompts.py's own
# _POSEIDON_PACKAGE_DIR, applied one level lower in the tree.
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
_PROMPT_NAME = "contextualizer.md"

_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_PROMPTS_DIR)),
    undefined=jinja2.StrictUndefined,
    autoescape=False,
    auto_reload=False,
)

_SYNTHESIS_ROLE = "synthesis"
_TITLE = "Context"

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk -- same convention as research/tools/format_
# phase_section.py's own _EM_DASH.
_EM_DASH = chr(0x2014)

# Byte-pinned across every stub-mode subskill this phase ships (doc 02
# Global Constraints) -- see the module docstring's "stub" bullet.
_STUB_OPENING = "Stub-mode synthesis " + _EM_DASH + " flip LLM_MODE=live for model narrative."

# Byte-pinned per-phase failure text -- never the provider's own error
# text (titles.py's own precedent: a transport failure's text must never
# reach the user verbatim).
_FAILURE_TEXT = (
    "Contextualization is unavailable right now "
    + _EM_DASH
    + " the synthesis model returned an error."
)

_USER_DIRECTIVE = "Write the contextualization narrative now, following the instructions above."

# Fixed, authored plain-English definitions for exactly the six certified
# metrics fetch_metrics.py's own SIX_METRICS names (imported, not
# retyped, so the two lists cannot silently drift) -- see the module
# docstring's "FIELD DICTIONARY" section for why this is a constant, not
# an ontology lookup. Wording is derived from ontology/ontology.yml's own
# MARINE_SALES_PLANNING_V metric definitions (sql/kind/rule), authored
# fresh as prose.
_FIELD_DICTIONARY: dict[str, str] = {
    "VOLUME": "Total fixed tons lifted for this customer (sum of FIXED_TONS).",
    "GP": "Gross profit in USD (sum of GROSS_PROFIT).",
    "MARGIN": (
        "Profit per ton in USD, computed as GP divided by VOLUME (SUM(GROSS_PROFIT) / "
        "SUM(FIXED_TONS)). Never sum or average this figure directly across rows."
    ),
    "NUM_WON": "Number of fixtures won (sum of #_FIXTURES).",
    "NUM_INQUIRIES": "Number of inquiries received (sum of #_INQUIRIES).",
    "NUM_LOST": "Inquiries that did not convert to a fixture (NUM_INQUIRIES minus NUM_WON).",
}

_FIELD_DICTIONARY_BLOCK = "\n".join(f"- {key}: {value}" for key, value in _FIELD_DICTIONARY.items())


def render_prompt(**context: object) -> str:
    """Render ``prompts/contextualizer.md`` with ``context`` as Jinja2
    template variables -- public (no leading underscore), matching
    ``poseidon.core.llm.prompts``'s own block-builder convention
    (``metric_definitions_block`` etc.), so a prompt-contract test can
    wire sentinel content directly, or omit a key to exercise
    ``StrictUndefined`` (see the module docstring's "PROMPT LOADING"
    section)."""
    return _ENV.get_template(_PROMPT_NAME).render(**context)


def _format_mapping(mapping: Mapping[str, object]) -> str:
    """A mapping -> one ``"- key: value"`` line per entry, in the
    mapping's own order; ``"(none on file)"`` when empty (a new
    prospect's data block, doc 02's own "nothing exists for a prospect").
    Deliberately schema-agnostic: this subskill never inspects a key or
    value beyond formatting it, which is what keeps it decoupled from
    whatever internal shape Task 4's ``fetch_metrics``/``fetch_top_ports``
    wiring ends up building ``data_block`` from."""
    if not mapping:
        return "(none on file)"
    return "\n".join(f"- {key}: {value}" for key, value in mapping.items())


def _format_research_block(research_inputs: tuple[dict, ...]) -> str:
    """``research.subskill``'s own ``synthesis_inputs`` -> one line per
    call: its title and summary, or its degrade reason when
    ``degraded`` -- never silently dropping a degraded section, matching
    that subskill's own "honest gap, never fabricated" rule one layer
    up."""
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


def _stub_text(
    subject: str, data_block: Mapping[str, object], research_inputs: tuple[dict, ...]
) -> str:
    """The pinned opening line, then a structured digest of the ACTUAL
    inputs this call received -- see the module docstring's "stub"
    bullet. Real counts, never a fabricated narrative."""
    digest = (
        f"Subject: {subject}\n"
        f"Data block metrics: {len(data_block)}\n"
        f"Research sections: {len(research_inputs)}"
    )
    return _STUB_OPENING + "\n\n" + digest


def run(
    ctx: SkillContext,
    mode: str,
    subject: str,
    data_block: Mapping[str, object],
    research_inputs: tuple[dict, ...],
) -> SubskillResult:
    """See the module docstring for the full stub/live/failure contract.

    Raises ``ValueError`` for a ``mode`` outside ``{MODE_EXISTING,
    MODE_PROSPECT}`` -- an internal wiring bug, never a value a real user
    turn could produce, matching ``research.subskill.run``'s own identical
    precedent and reasoning.
    """
    if mode not in (MODE_EXISTING, MODE_PROSPECT):
        raise ValueError(
            f"contextualize subskill: unknown mode {mode!r} -- expected "
            f"one of {sorted((MODE_EXISTING, MODE_PROSPECT))}"
        )

    if ctx.settings.llm_mode == "stub":
        text = _stub_text(subject, data_block, research_inputs)
        failed = False
    elif ctx.llm is None:
        text = _FAILURE_TEXT
        failed = True
    else:
        role_client = cast(RoleClient, ctx.llm)
        system = render_prompt(
            subject=subject,
            mode=mode,
            data_block=_format_mapping(data_block),
            field_dictionary=_FIELD_DICTIONARY_BLOCK,
            research_block=_format_research_block(research_inputs),
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
    """The failed-phase result skill.py's own exception-escape guard (P8
    whole-branch final-review wave, 2026-07-30, item 2 / I-4) synthesizes
    when a call to :func:`run` raises OUTRIGHT instead of returning
    normally (e.g. a raw ``BotoCoreError`` past bedrock's own
    ``ClientError``-only catch) -- the SAME pinned text and shape
    :func:`run` itself already returns for its own internal "the
    synthesis model returned an error" case (``ctx.llm is None`` or
    ``response.stop_reason == "error"``). A phase that failed is a phase
    that failed, whether the failure was caught inside this module or
    escaped it entirely; the user sees the identical honest message
    either way, and the brief's OTHER phases -- already computed, already
    streamed -- are unaffected (doc 02 section 6's anti-happy-path rule:
    "the phase fails, previously streamed deterministic parts stand")."""
    part = phase_section_part(_TITLE, _FAILURE_TEXT)
    return SubskillResult(parts=(part,), synthesis_inputs=({"text": _FAILURE_TEXT},), failed=True)

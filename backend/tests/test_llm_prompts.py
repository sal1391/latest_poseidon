"""Tests for Phase 5 Task 2 (doc 03 sections 3-4): PromptRegistry's Jinja2
loading/rendering, the router system prompt's dynamic guardrail content, the
fixed system-prompt assembly order, and render_state_block's deterministic
plain-text rendering of conversation state -- the prompt layer Task 4's
agent loop assembles every request from.

Non-ASCII characters in expected/pinned strings are written as explicit
``\\uXXXX`` escapes rather than typed literals, the same convention
``test_llm_types_roles.py`` and the Phase 4 parsing suites use: an em dash,
an en dash and a hyphen are visually indistinguishable in most editors, so a
byte-pinned message that used a typed character could silently pin the
wrong codepoint. ``test_llm_prompts_module_files_are_ascii_on_disk``
enforces that for this file and ``prompts.py``. ``router/system.md`` is
deliberately NOT scanned: house rules let prompt ``.md`` files carry real
punctuation (the ``models.yml`` precedent), so its guardrail prose uses a
typed em dash directly.
"""

from datetime import date
from pathlib import Path

import jinja2
import pytest

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.llm import prompts
from poseidon.core.llm.prompts import (
    DEFAULT_PROMPTS_DIR,
    PromptNotFoundError,
    PromptRegistry,
    assemble_system,
    metric_definitions_block,
    negative_constraints_block,
    render_state_block,
    skill_lines_block,
)
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.parsing.types import ParsedTurn, ParseIssue, ResolvedEntity
from poseidon.core.skills.context import ConversationSlots
from poseidon.core.skills.registry import SkillRegistry

SALES_ENTITY = "MARINE_SALES_PLANNING_V"

# The most cross-referenced negative constraint in this codebase (ontology.yml's
# own comments, infra/runbooks/local.md, and test_skill.py all name this exact
# pair) -- picked as the "stable one" the brief asks for precisely because it
# is already load-bearing evidence elsewhere, not a fragile one-off pick.
_PINNED_NEGATIVE_CONSTRAINT = "PORT_NM is not certified; use LOC_NM instead."


# ---------------------------------------------------------------------------
# PromptRegistry -- generic Jinja2 load/render behavior (throwaway fixture
# templates only; the real router/system.md is exercised in its own section
# below)
# ---------------------------------------------------------------------------


def _write_template(tmp_path: Path, relative: str, content: str) -> None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_render_basic_template_substitutes_context(tmp_path):
    _write_template(tmp_path, "hello/world.md", "Hello {{ name }}!")
    registry = PromptRegistry(tmp_path)

    assert registry.render("hello/world", name="Carlos") == "Hello Carlos!"


def test_render_is_pure_after_initial_load(tmp_path):
    """ "No filesystem access at render time beyond the initial load": once a
    name has been rendered once, deleting its source file must not break a
    second render of the SAME name -- proving the cached template, not the
    file, answers every later call (Environment(auto_reload=False))."""
    path = tmp_path / "hello" / "world.md"
    _write_template(tmp_path, "hello/world.md", "Hello {{ name }}!")
    registry = PromptRegistry(tmp_path)

    first = registry.render("hello/world", name="Carlos")
    path.unlink()
    second = registry.render("hello/world", name="Fable")

    assert first == "Hello Carlos!"
    assert second == "Hello Fable!"


def test_strict_undefined_raises_pinned_error_on_missing_variable(tmp_path):
    """The exact mechanism behind "missing placeholder raises", pinned on a
    controlled fixture template so it never depends on the real router
    prompt's own placeholder ordering (see the router-prompt section below
    for the integration-level version of this contract)."""
    _write_template(tmp_path, "hello/world.md", "Hello {{ name }}!")
    registry = PromptRegistry(tmp_path)

    with pytest.raises(jinja2.exceptions.UndefinedError) as err:
        registry.render("hello/world")

    assert str(err.value) == "'name' is undefined"


def test_render_missing_prompt_raises_pinned_not_found_error(tmp_path):
    """A name with no matching ``<name>.md`` file is a framework-clear
    :class:`PromptNotFoundError` naming the prompt and the search directory
    -- not a raw ``jinja2.TemplateNotFound`` escaping with its own search-path
    formatting (matches ``ModelProfileError``'s wrapping of ``yaml.YAMLError``
    in ``roles.py``)."""
    registry = PromptRegistry(tmp_path)

    with pytest.raises(PromptNotFoundError) as err:
        registry.render("nonexistent/prompt")

    assert str(err.value) == f"prompt 'nonexistent/prompt' not found under {tmp_path}"


# ---------------------------------------------------------------------------
# DEFAULT_PROMPTS_DIR -- packaged default, mirrors roles.py's
# DEFAULT_MODELS_PATH three-parent-hop resolution
# ---------------------------------------------------------------------------


def test_default_prompts_dir_exists_under_poseidon_config():
    """Ships inside the ``poseidon`` package, same reasoning and the same
    three-``.parent`` chain as ``roles.py``'s ``DEFAULT_MODELS_PATH`` (doc 03
    names repo-root ``config/``; the phase-5 plan deviates deliberately so
    the directory is packaged with every image and needs no extra mount)."""
    assert DEFAULT_PROMPTS_DIR.is_dir()
    assert DEFAULT_PROMPTS_DIR.parts[-3:] == ("poseidon", "config", "prompts")


def test_router_system_prompt_file_exists():
    assert (DEFAULT_PROMPTS_DIR / "router" / "system.md").is_file()


# ---------------------------------------------------------------------------
# router/system.md -- contract tests against the REAL ontology and the REAL
# skill registry (TM1 pattern, doc 03 section 4): the rendered prompt is
# re-verified every run against whatever those two sources actually contain
# today, so an ontology or skill-registry change that silently drops
# guardrail content fails this suite, not production.
# ---------------------------------------------------------------------------


def _render_real_router_prompt() -> str:
    entity = get_ontology().entity(SALES_ENTITY)
    skills = SkillRegistry.discover()
    registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    return registry.render(
        "router/system",
        metric_definitions=metric_definitions_block(entity),
        negative_constraints=negative_constraints_block(entity),
        skill_lines=skill_lines_block(skills),
    )


def test_router_prompt_names_every_skill_the_registry_discovers():
    """Dynamic against SkillRegistry.discover(), never a hardcoded skill
    list -- a future skill landing (or today's disabled customer_insight
    task turning on) changes what this test checks without anyone touching
    this file."""
    skills = SkillRegistry.discover()
    rendered = _render_real_router_prompt()

    assert skills.skill_ids  # sanity: discovery must find at least one today
    for skill_id in skills.skill_ids:
        assert skill_id in rendered


def test_router_prompt_contains_every_certified_metric_name():
    """Dynamic against the ontology's own metric inventory for the sales
    entity, never a hardcoded metric list -- a certified ontology upgrade
    changes what this test checks without anyone touching this file."""
    entity = get_ontology().entity(SALES_ENTITY)
    rendered = _render_real_router_prompt()

    assert entity.metrics  # sanity: the certified entity must have metrics
    for metric_name in entity.metrics:
        assert metric_name in rendered


def test_router_prompt_contains_pinned_negative_constraint_verbatim():
    rendered = _render_real_router_prompt()

    assert _PINNED_NEGATIVE_CONSTRAINT in rendered


def test_router_prompt_missing_context_raises_undefined_error():
    """The real prompt uses the same StrictUndefined mechanism pinned above
    -- verified here at the type level only (not a byte-pinned message,
    which would couple this test to the file's placeholder ORDER rather
    than its content)."""
    registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    with pytest.raises(jinja2.exceptions.UndefinedError):
        registry.render("router/system")


def test_router_prompt_charter_and_routing_rules_present():
    """Doc 03 section 4's own philosophy: prompt content is pinned so an
    edit that silently drops a required rule fails CI, not production.
    Covers the three prose requirements the brief names: deterministic-first
    charter, mode-is-advisory-never-a-filter (decision D19), and prefer
    clarification over guessing."""
    rendered = _render_real_router_prompt().lower()

    assert "deterministic" in rendered
    assert "advisory" in rendered
    assert "clarif" in rendered


# ---------------------------------------------------------------------------
# metric_definitions_block / negative_constraints_block / skill_lines_block
# -- the three block builders router/system.md's placeholders are filled
# with (unit-level, isolated from the real ontology/registry above)
# ---------------------------------------------------------------------------


def test_metric_definitions_block_one_line_per_metric_name_and_sql():
    entity = get_ontology().entity(SALES_ENTITY)

    block = metric_definitions_block(entity)

    lines = block.splitlines()
    assert len(lines) == len(entity.metrics)
    assert "MARGIN: SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0)" in lines


def test_negative_constraints_block_one_line_per_constraint():
    entity = get_ontology().entity(SALES_ENTITY)

    block = negative_constraints_block(entity)

    lines = block.splitlines()
    assert len(lines) == len(entity.negative_constraints)
    assert _PINNED_NEGATIVE_CONSTRAINT in lines


def test_skill_lines_block_one_line_per_registered_skill():
    registry = SkillRegistry.discover()

    block = skill_lines_block(registry)

    lines = block.splitlines()
    assert len(lines) == len(registry.skill_ids)
    for skill_id in registry.skill_ids:
        expected = f"{skill_id}: {registry.get(skill_id).description}"
        assert expected in lines


# ---------------------------------------------------------------------------
# assemble_system -- fixed order (doc 03 section 3): base, instruction,
# memory, state; labeled sections; empty inputs contribute NOTHING
# ---------------------------------------------------------------------------


def test_assemble_system_all_four_sections_present_in_order():
    result = assemble_system(
        base="BASE TEXT",
        user_instruction="INSTRUCTION TEXT",
        memory_doc="MEMORY TEXT",
        state_block="STATE TEXT",
    )

    assert result == (
        "=== BASE SYSTEM PROMPT ===\n"
        "BASE TEXT\n"
        "\n"
        "=== USER INSTRUCTION ===\n"
        "INSTRUCTION TEXT\n"
        "\n"
        "=== MEMORY ===\n"
        "MEMORY TEXT\n"
        "\n"
        "=== CONVERSATION STATE ===\n"
        "STATE TEXT"
    )
    # Order, not just presence: each header must precede the next section's.
    for earlier, later in (
        ("BASE SYSTEM PROMPT", "USER INSTRUCTION"),
        ("USER INSTRUCTION", "MEMORY"),
        ("MEMORY", "CONVERSATION STATE"),
    ):
        assert result.index(earlier) < result.index(later)


def test_assemble_system_empty_instruction_and_memory_produce_no_headers():
    result = assemble_system(
        base="BASE TEXT", user_instruction="", memory_doc="", state_block="STATE TEXT"
    )

    assert result == (
        "=== BASE SYSTEM PROMPT ===\nBASE TEXT\n\n=== CONVERSATION STATE ===\nSTATE TEXT"
    )
    assert "USER INSTRUCTION" not in result
    assert "MEMORY" not in result


def test_assemble_system_all_sections_empty_except_base():
    result = assemble_system(base="BASE TEXT", user_instruction="", memory_doc="", state_block="")

    assert result == "=== BASE SYSTEM PROMPT ===\nBASE TEXT"


def test_assemble_system_whitespace_only_counts_as_empty():
    """ "Empty inputs contribute NOTHING": whitespace-only carries no more
    information for the model than "", so it is dropped the same way."""
    result = assemble_system(base="BASE", user_instruction="   \n  ", memory_doc="", state_block="")

    assert result == "=== BASE SYSTEM PROMPT ===\nBASE"


def test_assemble_system_strips_leading_and_trailing_whitespace_from_content():
    result = assemble_system(base="  BASE  \n", user_instruction="", memory_doc="", state_block="")

    assert result == "=== BASE SYSTEM PROMPT ===\nBASE"


# ---------------------------------------------------------------------------
# render_state_block -- deterministic plain-text rendering of
# ConversationSlots + ParsedTurn (periods ISO, entities with tier/confidence,
# issues verbatim, deterministic ordering)
# ---------------------------------------------------------------------------


def test_render_state_block_empty_slots_golden():
    result = render_state_block(ConversationSlots(), None)

    assert result == "Mode: default"


def test_render_state_block_carried_customer_and_period_golden():
    slots = ConversationSlots(
        customer="MAERSK LINE",
        period_a=date(2026, 4, 1),
        period_b=date(2026, 5, 1),
    )

    result = render_state_block(slots, None)

    assert result == (
        "Mode: default\n"
        "Carried customer: MAERSK LINE\n"
        "Carried period A: 2026-04-01\n"
        "Carried period B: 2026-05-01"
    )


def test_render_state_block_parsed_turn_with_issues_golden():
    """The rich scenario bundling every documented property at once: entities
    with tier/confidence, an ISO half-open PeriodWindow, and two issues whose
    messages render verbatim in their original (non-alphabetical) tuple
    order -- proving no incidental sorting sneaks in."""
    slots = ConversationSlots(customer="MAERSK LINE", mode="default")
    parsed = ParsedTurn(
        normalized_text="top gp for maersk in a period with no data",
        slots=slots,
        period_a=PeriodWindow(start=date(2026, 4, 1), end=date(2026, 5, 1)),
        period_b=None,
        customer=ResolvedEntity(
            value="MAERSK LINE", source_text="maersk", confidence=1.0, tier="exact"
        ),
        port=ResolvedEntity(
            value="SINGAPORE", source_text="singapore", confidence=0.9333333333333332, tier="fuzzy"
        ),
        hints=(),
        issues=(
            ParseIssue(
                code="period_unavailable",
                message="No data available for April 2026.",
                candidates=(),
            ),
            ParseIssue(
                code="customer_ambiguous",
                message='Multiple customers match "maersk": MAERSK LINE, MAERSK BROKER.',
                candidates=("MAERSK LINE", "MAERSK BROKER"),
            ),
        ),
    )

    result = render_state_block(slots, parsed)

    assert result == (
        "Mode: default\n"
        "Carried customer: MAERSK LINE\n"
        "\n"
        "Resolved customer: MAERSK LINE (tier=exact, confidence=1.00)\n"
        "Resolved port: SINGAPORE (tier=fuzzy, confidence=0.93)\n"
        "Period: 2026-04-01..2026-05-01\n"
        "Issues:\n"
        "- [period_unavailable] No data available for April 2026.\n"
        '- [customer_ambiguous] Multiple customers match "maersk": MAERSK LINE, MAERSK BROKER.'
    )


def test_render_state_block_carried_port_region_topic_pass_through():
    slots = ConversationSlots(
        port="SINGAPORE",
        region="APAC",
        topic="pricing",
        pass_through=(("top_customer", "MAERSK LINE"), ("top_port", "SINGAPORE")),
    )

    result = render_state_block(slots, None)

    assert result == (
        "Mode: default\n"
        "Carried port: SINGAPORE\n"
        "Region: APAC\n"
        "Topic: pricing\n"
        "Pass-through: top_customer=MAERSK LINE, top_port=SINGAPORE"
    )


def test_render_state_block_compare_period_line():
    parsed = ParsedTurn(
        normalized_text="gp this ytd vs prior year",
        slots=ConversationSlots(),
        period_a=PeriodWindow(start=date(2026, 4, 1), end=date(2026, 5, 1)),
        period_b=PeriodWindow(start=date(2025, 4, 1), end=date(2025, 5, 1)),
        customer=None,
        port=None,
        hints=(),
        issues=(),
    )

    result = render_state_block(ConversationSlots(), parsed)

    assert result == (
        "Mode: default\n\nPeriod: 2026-04-01..2026-05-01\nCompare period: 2025-04-01..2025-05-01"
    )


def test_render_state_block_parsed_none_and_empty_parsed_are_equivalent():
    """A ParsedTurn that resolved nothing at all renders identically to
    ``parsed=None`` -- there is nothing turn-specific to show either way."""
    empty_parsed = ParsedTurn(
        normalized_text="hello",
        slots=ConversationSlots(),
        period_a=None,
        period_b=None,
        customer=None,
        port=None,
        hints=(),
        issues=(),
    )

    assert render_state_block(ConversationSlots(), empty_parsed) == render_state_block(
        ConversationSlots(), None
    )


# ---------------------------------------------------------------------------
# ASCII-only source, matching the Phase 4 parsing / Task 1 llm convention
# ---------------------------------------------------------------------------


def test_llm_prompts_module_files_are_ascii_on_disk():
    """Byte-pinned messages (the "is undefined" mechanism, the negative
    constraint, every render_state_block golden) stay pinned only if no
    look-alike codepoint can slip into either file -- see the module
    docstring for why ``router/system.md`` is deliberately excluded."""
    paths = (Path(prompts.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"

"""Prompt loading, guardrail-block rendering, and system-prompt assembly
(doc 03 sections 3-4): the layer between "prompts are versioned files, not
inline string constants" and the fixed per-turn assembly order every router
and synthesis call uses.

Three things live here:

:class:`PromptRegistry` loads and renders the Jinja2 ``.md`` files under
``poseidon/config/prompts/`` (doc 03 section 4's reversal of TM1's 160-line
``SYSTEM_PROMPT`` constant). Rendering is pure once a name has been loaded
once: the :class:`jinja2.Environment` is built with ``auto_reload=False``,
so ``get_template`` never re-stats the source file after its first
successful load -- the ONLY filesystem access ``render`` ever causes is that
first load per name, never a later call for the same name (proven by
``test_render_is_pure_after_initial_load``, which deletes the source file
between two renders of the same prompt). ``StrictUndefined`` turns a
forgotten context variable into an immediate, loud ``UndefinedError``
instead of a silently blank section in a live system prompt. No autoescape:
these render to plain text fed to a model, never to HTML.

``metric_definitions_block``/``negative_constraints_block``/
``skill_lines_block`` compute the three guardrail strings
``router/system.md`` interpolates with plain ``{{ }}`` placeholders (not a
Jinja loop) -- doc 03 section 4: "the router system prompt embeds two
ontology-derived blocks at render time... the guardrail text lives with the
ontology, not copy-pasted into prompts." They take already-resolved
``Entity``/``SkillRegistry`` objects rather than reaching into
``get_ontology()``/``SkillRegistry.discover()`` themselves, so this module
has no hidden I/O of its own and stays trivially testable against a
throwaway entity or registry.

``assemble_system``/``render_state_block`` build the fixed assembly order
(doc 03 section 3, items 1-5): base system prompt, user instruction, memory
document, then a structured conversation-state block combining items 4 and
5 (carried slots + the current turn's ``ParsedTurn``) into the one
``state_block`` string ``assemble_system`` places last.

``prompt_version``/``prompt_hash`` (Phase 6 Task 1, additive) feed doc 06
section 1's ``llm_calls.prompt_version``/``prompt_hash`` columns -- per-call
provenance: which prompt FILE version rendered, and a hash of the exact TEXT
a provider actually saw. ``prompt_version`` reads a template's version
marker from the raw ``.md`` file, never through :meth:`PromptRegistry.render`
-- the marker has to be read BEFORE rendering strips it, and reading the raw
file also means a version lookup costs no Jinja compilation and cannot raise
``StrictUndefined``'s missing-context error. The marker is a Jinja COMMENT,
``{# version: v1 #}``, not an HTML comment (``<!-- version: v1 -->``) -- the
one syntax Jinja actually strips at render time. An HTML comment would
render straight through into the text a provider sees, on every single
call, forever; a Jinja comment is gone before ``render()`` returns anything.
Both shipped templates (``router/system.md``, ``utility/title.md``) write it
with the trailing whitespace-trim marker, ``{# version: v1 -#}``: Jinja2's
default ``trim_blocks=False`` (this module's ``Environment`` sets neither
``trim_blocks`` nor ``lstrip_blocks``) removes the comment text but NOT the
newline immediately after it, so a bare ``{# ... #}`` first line would leave
a stray leading blank line in every render -- verified empirically while
building this feature, since it is exactly the kind of whitespace detail
that is wrong more often than it is obvious. The ``-#}`` form trims that
newline too, so the rendered template is byte-identical to a version with no
marker line at all. ``prompt_version`` itself accepts either form (with or
without the trim dash) since it reads text, not render output.
"""

import hashlib
import re
from pathlib import Path

import jinja2

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.ontology.models import Entity
from poseidon.core.parsing.types import ParsedTurn, ResolvedEntity
from poseidon.core.skills.context import ConversationSlots
from poseidon.core.skills.registry import SkillRegistry

# core/llm/prompts.py -> core/llm -> core -> poseidon: identical three-hop
# chain to roles.py's DEFAULT_MODELS_PATH (see that module's comment for the
# full rationale) -- config/ hangs directly off the poseidon package, not
# nested under core/, so both ship inside the package with no extra
# container mount.
_POSEIDON_PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_PROMPTS_DIR = _POSEIDON_PACKAGE_DIR / "config" / "prompts"


class PromptNotFoundError(Exception):
    """A :meth:`PromptRegistry.render` name has no matching ``<name>.md``
    file under the registry's prompts directory. Raised at render time."""


class PromptRegistry:
    """Loads and renders the versioned Jinja2 prompt files under
    ``prompts_dir`` (doc 03 section 4).

    Construction touches no file: :class:`jinja2.Environment` and
    :class:`jinja2.FileSystemLoader` only remember the directory. The first
    :meth:`render` call for a given ``name`` reads and compiles
    ``<name>.md``; every later call for that same name is served from the
    environment's in-memory cache with zero filesystem access, because the
    environment is built with ``auto_reload=False`` (see the module
    docstring).
    """

    def __init__(self, prompts_dir: Path) -> None:
        self._prompts_dir = prompts_dir
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(prompts_dir)),
            undefined=jinja2.StrictUndefined,
            autoescape=False,
            auto_reload=False,
        )

    def render(self, name: str, /, **context) -> str:
        """Render ``<name>.md`` (e.g. ``name="router/system"``) with
        ``context`` as Jinja2 template variables.

        ``StrictUndefined`` means any placeholder in the template with no
        matching keyword argument raises ``jinja2.exceptions.UndefinedError``
        immediately, naming the missing variable -- never a silently blank
        section in a prompt an LLM is about to see.
        """
        try:
            template = self._env.get_template(f"{name}.md")
        except jinja2.TemplateNotFound:
            raise PromptNotFoundError(
                f"prompt '{name}' not found under {self._prompts_dir}"
            ) from None
        return template.render(**context)


# ``{# version: v1 -#}`` (or, tolerated on read, the untrimmed
# ``{# version: v1 #}``) -- see the module docstring for the full mechanism
# and why a Jinja comment, not an HTML one. Anchored to the WHOLE first line
# (after stripping only the line ending) so a version marker has to be
# exactly that line and nothing else -- a template author writing ordinary
# prose or a heading that happens to mention "version:" must not be
# misread as one.
_VERSION_COMMENT_RE = re.compile(r"^\{#-?\s*version:\s*(\S+)\s*-?#\}\s*$")

# What a template with no recognizable version marker reports -- doc 06's
# "version of the prompt file used" column has to hold SOMETHING for every
# call, and "v0" says "unversioned" without inventing a version that was
# never declared.
_UNVERSIONED = "v0"


def prompt_version(prompts_dir: Path, name: str) -> str:
    """The version marker on ``<name>.md``'s first line, or ``"v0"`` when
    that line is not one (no marker at all, or a malformed one) -- see the
    module docstring for the exact syntax and why this reads the raw file
    rather than rendering it.

    A ``name`` with no matching file raises ``FileNotFoundError`` (via the
    plain ``open()`` below) rather than also defaulting to ``"v0"``: unlike
    a missing MARKER -- a low-stakes miss on an existing, presumably-correct
    prompt file -- a missing FILE means ``prompts_dir`` or ``name`` is wrong,
    the same class of deployment fault :meth:`PromptRegistry.render` fails
    loudly on via :class:`PromptNotFoundError`. Silently reporting "v0" for
    that would hide a broken prompts directory behind a plausible-looking
    version string.
    """
    with open(prompts_dir / f"{name}.md", encoding="utf-8") as handle:
        first_line = handle.readline()
    match = _VERSION_COMMENT_RE.match(first_line.rstrip("\r\n"))
    return match.group(1) if match else _UNVERSIONED


def prompt_hash(rendered: str) -> str:
    """The sha256 hexdigest of ``rendered`` -- doc 06's ``llm_calls.
    prompt_hash``, "hash of the rendered prompt actually sent". Hashing the
    RENDERED text, not the template source, is the point: two turns on the
    same template version can still carry different context (a different
    memory document, different carried slots), and the hash is what lets a
    reviewer confirm two calls saw byte-identical input rather than merely
    the same template file.
    """
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def metric_definitions_block(entity: Entity) -> str:
    """One line per certified metric on ``entity``: its name and its
    certified SQL expression, in the ontology's own file order (the
    ``dict`` PyYAML parsed ``entity.metrics`` from preserves insertion
    order, so this needs no explicit sort to stay deterministic run to
    run -- matching ``Entity.dimensions()``/``measures()``'s own "in file
    order" convention).

    The certified SQL expression, and only it, is what this block carries.
    :class:`~poseidon.core.ontology.models.Metric` does also hold ``rule``
    -- router-facing prose on 3 of the sales entity's 7 metrics (MARGIN,
    NUM_LOST, WIN_RATE), e.g. "NEVER sum or average a raw margin column" --
    and leaving it out is a v1 scope decision, not an absence. The router's
    job here is to pick a skill and fill its ARGUMENTS; it never writes SQL,
    and the certified expression (rules and all) is applied downstream by
    the deterministic skill, so the prose guards a step the model does not
    take. It is also the most expensive kind of prompt text to be wrong
    about -- paid on every turn, and unfalsifiable offline. Phase 6 revisits
    it against live router evidence: a real misroute those three rules would
    have prevented is the thing that decides it.
    """
    return "\n".join(f"{metric.name}: {metric.sql}" for metric in entity.metrics.values())


def negative_constraints_block(entity: Entity) -> str:
    """One line per observed negative constraint on ``entity``: the
    hallucinated name and its certified replacement, in the ontology's own
    file order (``entity.negative_constraints`` is already a ``list`` in
    that order).

    Neither ``wrong`` nor ``right`` is re-quoted here: several certified
    ``right`` values already carry their own required double quotes (e.g.
    ``"#_FIXTURES"``, a Snowflake quoted identifier) while most do not (e.g.
    ``LOC_NM``) -- adding a second layer of quoting around every value would
    double-quote the first group and misquote the second. Passing both
    through unchanged renders correctly either way.
    """
    return "\n".join(
        f"{constraint.wrong} is not certified; use {constraint.right} instead."
        for constraint in entity.negative_constraints
    )


def skill_lines_block(registry: SkillRegistry) -> str:
    """One line per registered skill: its id and its router-facing
    ``SKILL_META['description']``, in ``registry.skill_ids`` order.

    That order is already deterministic -- ``SkillRegistry.discover()``
    walks task and skill directories in sorted order by its own contract
    (see ``registry.py``'s module docstring) -- so this needs no sort of
    its own.
    """
    return "\n".join(
        f"{skill_id}: {registry.get(skill_id).description}" for skill_id in registry.skill_ids
    )


# Fixed assembly order (doc 03 section 3, items 1-4; item 5 folds into
# ``state_block`` -- see render_state_block). Headers are plain ASCII
# dividers, not Markdown, so they can never collide with a heading already
# inside ``base`` (the rendered router/system.md, which owns its own
# "# "/"## " headings).
_HEADER_BASE = "=== BASE SYSTEM PROMPT ==="
_HEADER_INSTRUCTION = "=== USER INSTRUCTION ==="
_HEADER_MEMORY = "=== MEMORY ==="
_HEADER_STATE = "=== CONVERSATION STATE ==="


def assemble_system(base: str, user_instruction: str, memory_doc: str, state_block: str) -> str:
    """Join the four sections of doc 03 section 3's fixed assembly order,
    each under its own labeled header, in order: base, instruction, memory,
    state.

    A section whose content is empty -- or purely whitespace, which carries
    exactly as little information for the model as "" does -- contributes
    NOTHING: no header, no blank placeholder, nothing at all. This is what
    lets ``user_instruction``/``memory_doc`` stay unpopulated in Phase 5
    (Phase 9/13 own filling them in) without every system prompt carrying
    two permanently-empty sections.
    """
    sections = (
        (_HEADER_BASE, base),
        (_HEADER_INSTRUCTION, user_instruction),
        (_HEADER_MEMORY, memory_doc),
        (_HEADER_STATE, state_block),
    )
    parts = []
    for header, content in sections:
        stripped = content.strip()
        if stripped:
            parts.append(f"{header}\n{stripped}")
    return "\n\n".join(parts)


def render_state_block(slots: ConversationSlots, parsed: ParsedTurn | None) -> str:
    """Deterministic plain-text rendering of the carried conversation state
    plus the current turn's parse (doc 03 section 3 items 4-5, combined into
    the one block ``assemble_system`` places last).

    Two independently-omittable groups, in this fixed field order:

    - From ``slots`` (always rendered; carried across turns): mode (never
      empty -- it is the router's advisory D19 signal even when nothing
      else carried), then customer/port/period_a/period_b/region/topic/
      pass_through, each only when set.
    - From ``parsed`` (only when not ``None``, and only the fields it
      actually resolved): the freshly resolved customer/port entities with
      tier and confidence, the period window(s) rendered ISO half-open
      (``start..end``, matching ``format_parts.py``'s proof-line
      convention), then every issue's message verbatim, one per line, in
      the order the parser produced them -- never re-sorted or
      re-summarized, so a clarifying question can quote it directly.

    ``slots.period_a``/``period_b`` are single first-of-period DATES (see
    ``ConversationSlots``'s own docstring), not a window pair, so they
    render as two independent ISO dates -- unlike ``parsed.period_a``/
    ``period_b``, which ARE half-open :class:`PeriodWindow` pairs and render
    as a range.
    """
    slot_lines = _render_slots(slots)
    parsed_lines = _render_parsed(parsed) if parsed is not None else []
    groups = [lines for lines in (slot_lines, parsed_lines) if lines]
    return "\n\n".join("\n".join(lines) for lines in groups)


def _render_slots(slots: ConversationSlots) -> list[str]:
    lines = [f"Mode: {slots.mode}"]
    if slots.customer is not None:
        lines.append(f"Carried customer: {slots.customer}")
    if slots.port is not None:
        lines.append(f"Carried port: {slots.port}")
    if slots.period_a is not None:
        lines.append(f"Carried period A: {slots.period_a.isoformat()}")
    if slots.period_b is not None:
        lines.append(f"Carried period B: {slots.period_b.isoformat()}")
    if slots.region is not None:
        lines.append(f"Region: {slots.region}")
    if slots.topic is not None:
        lines.append(f"Topic: {slots.topic}")
    if slots.pass_through:
        pairs = ", ".join(f"{label}={value}" for label, value in slots.pass_through)
        lines.append(f"Pass-through: {pairs}")
    return lines


def _render_parsed(parsed: ParsedTurn) -> list[str]:
    lines = []
    if parsed.customer is not None:
        lines.append(_render_entity("Resolved customer", parsed.customer))
    if parsed.port is not None:
        lines.append(_render_entity("Resolved port", parsed.port))
    if parsed.period_a is not None:
        lines.append(_render_window("Period", parsed.period_a))
    if parsed.period_b is not None:
        lines.append(_render_window("Compare period", parsed.period_b))
    if parsed.issues:
        lines.append("Issues:")
        lines.extend(f"- [{issue.code}] {issue.message}" for issue in parsed.issues)
    return lines


def _render_entity(label: str, entity: ResolvedEntity) -> str:
    return f"{label}: {entity.value} (tier={entity.tier}, confidence={entity.confidence:.2f})"


def _render_window(label: str, window: PeriodWindow) -> str:
    return f"{label}: {window.start.isoformat()}..{window.end.isoformat()}"

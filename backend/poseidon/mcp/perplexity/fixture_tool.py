"""FixtureResearchTool: a :class:`~poseidon.mcp.registry.ResearchTool` that
answers from a recorded fixture instead of a real transport (doc 02 section
7, Phase 7 Task 4).

Why this exists (the AMENDED app.py wiring, plan amendment 9a5ca1b): a real
``PERPLEXITY_API_KEY`` can exist in an operator's ambient environment (it did
the night this task shipped) while the DEMO/dev surface should still never
burn a live API call just because a key happens to be present -- key
PRESENCE is the wrong gate for that, so ``api/app.py`` gates the choice on
``settings.llm_mode`` instead (the same switch that already governs the LLM
provider, decision precedent doc 06's "stub LLM mode throughout"): ``"stub"``
installs an instance of this class as :class:`~poseidon.mcp.registry
.ToolServerRegistry`'s ``overrides={"research": ...}``, bypassing transport
resolution entirely (see that module's own docstring, "how local dev runs
without a Perplexity key"); ``"live"`` lets the registry resolve a real
transport per ``TOOL_TRANSPORT_PERPLEXITY`` as designed. This class is also
what ``test_llm_loop.py``'s own offline stub executor injects to run
``routing_cases.yml``'s ``pivot_to_research_with_carry`` end to end through
the REAL :class:`~poseidon.core.skills.registry.SkillRegistry`.

REUSE, NOT DUPLICATION (the same convention ``mcp_client.py``'s own module
docstring establishes for its transport): this class differs from
:class:`~poseidon.mcp.perplexity.adapter.PerplexityDirectAdapter` only in
HOW it obtains an envelope -- reading a fixture file off disk rather than
making an HTTP call -- never in what it does with one once it has it. Once
``choices[0].message.content`` is in hand, this feeds the EXACT SAME shared
:func:`~poseidon.mcp.perplexity.adapter.load_schema`/:func:`~poseidon.mcp
.perplexity.adapter.parse_with_recovery`/:func:`~poseidon.mcp.perplexity
.adapter.validate_and_normalize` pipeline every other transport in this
package reuses -- no JSON parsing, schema validation, or truncation-recovery
logic of its own.

Never raises: reading a hand-authored, already-valid fixture (the shipped
``fixtures/clean.json``) never realistically fails, but a caller CAN point
``fixture_name``/``fixtures_dir`` at something else (a test does, exactly to
prove these paths), so every failure this class can produce -- the fixture
file is missing (two different reasons depending on HOW it went missing --
see "SCHEMA-NAME FIXTURE ROUTING" below), the envelope does not have the
expected shape, the content does not parse, the parsed content fails schema
validation -- degrades exactly like a real transport would rather than
raising mid-turn. The two reasons that overlap with the direct adapter's own
reason constants
(:data:`~poseidon.mcp.perplexity.adapter.REASON_PARSE_FAILED`,
:data:`~poseidon.mcp.perplexity.adapter.REASON_INVALID_SCHEMA`) are the
SAME public symbols, imported directly rather than redeclared here
(final-review wave item 4 -- this module used to keep its own byte-
identical private copies, a third independent copy of the same two
strings ``mcp_client.py`` also used to keep; all three are now one shared
pair): a caller reading a degrade reason should not have to know or care
which transport (real or fixture-backed) is behind ``ctx.tools.research``
to recognize "the response did not parse" or "the response was missing
required fields", and importing the same name is what makes that true by
construction instead of by three modules happening to agree.

This guarantee is scoped to a LOADED schema (final-review wave item 7):
an unknown ``schema_name`` raises ``FileNotFoundError`` straight out of
``load_schema`` (called near the end of :meth:`FixtureResearchTool.search`,
after the fixture file itself has already been read and parsed
successfully) -- uncaught, by design; see adapter.py's own module
docstring for why that is a deployment bug, not a fifth degrade rule.

SCHEMA-NAME FIXTURE ROUTING (Phase 8 Task 2, additive): the paragraph above
is about ``load_schema``'s own file -- ``schemas/{schema_name}.json``, the
JSON Schema every transport validates a response against -- and that rule
is UNCHANGED here: a ``schema_name`` with no such file still raises
``FileNotFoundError`` uncaught, exactly as before. This paragraph is about
a DIFFERENT file, read EARLIER in :meth:`FixtureResearchTool.search`: the
recorded FIXTURE DATA this class stands in for a real transport with
(``fixtures/{stem}.json``). When a caller leaves ``fixture_name`` at its
default (``None`` as of Task 2 -- see :meth:`FixtureResearchTool.__init__`),
``stem`` is derived from ``schema_name`` itself
(:func:`_resolve_fixture_stem`): ``web_research`` keeps reading the Phase 7
file every existing caller already depends on (``fixtures/clean.json``, via
``_DEFAULT_FIXTURE_NAME``) so ``api/app.py``'s ``FixtureResearchTool()`` and
this module's own pre-Task-2 test suite need no change; every OTHER
``schema_name`` (the four Task 2 schemas today, whatever a future task ships
next) routes to ``fixtures/{schema_name}_clean.json`` by NAMING CONVENTION
alone -- no registry to maintain, no per-schema branch in this module.

A ``schema_name`` with no fixture authored for it yet (a real gap if a
future phase ships a new schema before its fixture, or simply a typo)
degrades with reason ``"no fixture for schema"`` instead of raising --
DELIBERATELY SOFTER than ``load_schema``'s own uncaught raise above, for a
reason specific to what this class is FOR: it is the demo/offline-dev path
(see this module's opening paragraph -- installed whenever
``settings.llm_mode == "stub"``, no live key involved at all, key PRESENCE
never the gate), and a missing DATA recording is exactly the kind of gap
that path must survive to keep demoing everything ELSE that already has a
fixture -- never crash a whole turn over one missing recording. A missing
SCHEMA file, by contrast, is a wiring bug no fixture could paper over
anyway (there would be nothing to validate the response against) -- so
that one stays a loud, uncaught failure regardless of transport, exactly
as adapter.py's own docstring argues for the real "direct"/"mcp"
transports; this class does not soften THAT rule, only add a softer one
of its own for a file the real transports have no equivalent of at all.
Never conflate the two: this class can be asked for a ``schema_name``
whose DATA fixture is missing (soft degrade, this paragraph) while its
SCHEMA is present, or the reverse (schema missing -- uncaught raise,
whether or not a fixture happens to exist for it) -- they are looked up
from two different directories, at two different points in ``search()``,
guarded by two different rules, for two different reasons.

An explicit ``fixture_name=`` (Task 2 leaves this override exactly as it
was) skips schema-name routing entirely and reads that literal file; a
miss there degrades with the ORIGINAL reason, ``"fixture file not found"``
-- a caller who named a specific file gets the honest "that file is not
there" answer, not "no fixture for schema" (which would misreport WHY:
nothing about ``schema_name`` was even consulted on this path).
"""

import json
from pathlib import Path
from typing import Any

from poseidon.mcp.perplexity.adapter import (
    REASON_INVALID_SCHEMA,
    REASON_PARSE_FAILED,
    load_schema,
    parse_with_recovery,
    validate_and_normalize,
)
from poseidon.mcp.registry import ResearchResult

_TRANSPORT = "fixture"
_DEFAULT_FIXTURE_NAME = "clean"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Byte-pinned degrade reasons (house rule) that are genuinely fixture-
# specific -- these stay private/local. The two SHARED reasons (final-
# review wave item 4) are no longer declared here at all -- REASON_PARSE_
# FAILED/REASON_INVALID_SCHEMA are imported directly from adapter.py
# above; see the module docstring's "Never raises" paragraph.
_REASON_FILE_NOT_FOUND = "fixture file not found"
_REASON_MALFORMED_ENVELOPE = "malformed fixture envelope"

# Task 2, additive: the auto-routed sibling of _REASON_FILE_NOT_FOUND above
# -- see the module docstring's "SCHEMA-NAME FIXTURE ROUTING" paragraph for
# why a missing fixture gets a DIFFERENT reason depending on whether the
# caller named a specific file (_REASON_FILE_NOT_FOUND) or the miss was
# schema_name-driven (this one).
_REASON_NO_FIXTURE_FOR_SCHEMA = "no fixture for schema"

# The one schema_name that keeps its Phase 7 fixture filename under
# auto-routing rather than following the schema_name + "_clean" convention
# every other schema_name gets -- see _resolve_fixture_stem and the module
# docstring's routing paragraph for why: every pre-Task-2 caller of
# FixtureResearchTool() already depends on this exact file.
_WEB_RESEARCH_SCHEMA = "web_research"


def _resolve_fixture_stem(schema_name: str) -> str:
    """``schema_name`` -> the fixture file's stem (``fixtures/{stem}.json``),
    under SCHEMA-NAME AUTO-ROUTING only -- see the module docstring's own
    section of that name for when this function is even consulted (never,
    when a caller pinned an explicit ``fixture_name`` at construction).

    ``web_research`` is the one deliberate exception, mapped to
    ``_DEFAULT_FIXTURE_NAME`` ("clean") rather than the general
    ``f"{schema_name}_clean"`` pattern every other schema_name follows --
    preserving the exact Phase 7 filename (``fixtures/clean.json``) so
    nothing that already depends on it (``api/app.py``, this module's own
    pre-Task-2 test suite, ``test_llm_loop.py``) needs to change.
    """
    if schema_name == _WEB_RESEARCH_SCHEMA:
        return _DEFAULT_FIXTURE_NAME
    return f"{schema_name}_clean"


def _degrade(reason: str) -> ResearchResult:
    """Every degrade path funnels through here -- see
    :func:`poseidon.mcp.perplexity.adapter._degrade`'s own docstring for the
    identical zero-items ``raw_digest`` convention this mirrors."""
    return ResearchResult(
        items=(),
        raw_digest=f"0 results via {_TRANSPORT}",
        transport=_TRANSPORT,
        degraded=True,
        degrade_reason=reason,
    )


def _extract_content(envelope: Any) -> str | None:
    """The recorded fixture's own outer shape is the direct adapter's HTTP
    chat-completion envelope (``choices[0].message.content``) -- the exact
    shape ``fixtures/*.json`` was already authored in for Task 2's own
    tests, reused here verbatim rather than inventing a second on-disk
    format for the same recorded content. ``None`` on any shape mismatch
    (never raises: an ``IndexError``/``KeyError``/``TypeError`` on a
    malformed envelope is a degrade, not a crash).
    """
    try:
        content = envelope["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


class FixtureResearchTool:
    """Fixture-backed implementation of :class:`poseidon.mcp.registry
    .ResearchTool`. See the module docstring for why this exists and the
    reuse/degrade rules.

    ``query``/``recency_days`` are accepted (the ``ResearchTool`` protocol's
    fixed, keyword-only call shape) but never consulted: this is a CANNED
    answer, not a real search, so it answers identically regardless of what
    a caller asks -- proof of exactly that is this class's own test suite's
    ``test_search_answers_identically_regardless_of_query``.

    ``schema_name`` (``search``'s own keyword, not a constructor argument)
    is a partial exception to that "never consulted" rule as of Task 2: it
    still never changes WHAT is answered with (still the one fixture file's
    recorded content, verbatim), but it now can select WHICH fixture file
    that is -- see :func:`_resolve_fixture_stem` and the module docstring's
    "SCHEMA-NAME FIXTURE ROUTING" section -- whenever ``fixture_name`` was
    left at its default. Passing an explicit ``fixture_name`` (as this
    class's own pre-Task-2 tests do throughout, to reach specific recorded
    fixtures like ``truncated_mid_string``) opts back out of that routing
    entirely: ``schema_name`` then only ever reaches :func:`~poseidon.mcp
    .perplexity.adapter.load_schema`, exactly as before Task 2.
    """

    def __init__(
        self,
        fixture_name: str | None = None,
        fixtures_dir: Path | None = None,
    ) -> None:
        """``fixture_name=None`` (the default, changed from the literal
        ``"clean"`` by Task 2) means "auto-route from ``schema_name`` at
        search time" -- see :func:`_resolve_fixture_stem`. Any other value
        pins a specific file and disables routing for the lifetime of this
        instance, the exact pre-Task-2 behavior every existing caller that
        passes ``fixture_name=`` explicitly (this module's own test suite)
        still gets, unchanged.
        """
        self._fixture_name = fixture_name
        self._fixtures_dir = _FIXTURES_DIR if fixtures_dir is None else fixtures_dir

    def search(
        self, *, query: str, schema_name: str, recency_days: int | None = None
    ) -> ResearchResult:
        """Read the resolved fixture file, parse+recover+validate it
        through the shared pipeline. Never raises for a missing/malformed
        FIXTURE -- see the module docstring's "Never raises" and
        "SCHEMA-NAME FIXTURE ROUTING" paragraphs; ``load_schema`` below
        still raises uncaught for a missing SCHEMA, also per the module
        docstring, unchanged by Task 2.
        """
        if self._fixture_name is not None:
            stem = self._fixture_name
            not_found_reason = _REASON_FILE_NOT_FOUND
        else:
            stem = _resolve_fixture_stem(schema_name)
            not_found_reason = _REASON_NO_FIXTURE_FOR_SCHEMA

        path = self._fixtures_dir / f"{stem}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _degrade(not_found_reason)

        content = _extract_content(raw)
        if content is None:
            return _degrade(_REASON_MALFORMED_ENVELOPE)

        parsed = parse_with_recovery(content)
        if parsed is None:
            return _degrade(REASON_PARSE_FAILED)

        schema = load_schema(schema_name)
        normalized = validate_and_normalize(parsed, schema)
        if normalized is None:
            return _degrade(REASON_INVALID_SCHEMA)
        items, summary = normalized

        return ResearchResult(
            items=items,
            raw_digest=f"{len(items)} results via {_TRANSPORT}",
            transport=_TRANSPORT,
            summary=summary,
        )

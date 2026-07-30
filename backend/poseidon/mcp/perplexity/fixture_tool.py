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
prove these paths), so every failure this class can produce -- the file is
missing, the envelope does not have the expected shape, the content does not
parse, the parsed content fails schema validation -- degrades exactly like a
real transport would rather than raising mid-turn. The two reasons that
overlap with the direct adapter's own reason constants
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
    """

    def __init__(
        self,
        fixture_name: str = _DEFAULT_FIXTURE_NAME,
        fixtures_dir: Path | None = None,
    ) -> None:
        self._fixture_name = fixture_name
        self._fixtures_dir = _FIXTURES_DIR if fixtures_dir is None else fixtures_dir

    def search(
        self, *, query: str, schema_name: str, recency_days: int | None = None
    ) -> ResearchResult:
        """Read ``{fixtures_dir}/{fixture_name}.json``, parse+recover+
        validate it through the shared pipeline. Never raises -- see the
        module docstring's "Never raises" paragraph for exactly which four
        failures degrade instead of propagating.
        """
        path = self._fixtures_dir / f"{self._fixture_name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _degrade(_REASON_FILE_NOT_FOUND)

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

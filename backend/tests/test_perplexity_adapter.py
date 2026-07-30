"""Tests for Phase 7 Task 2 (doc 02 section 7, decision D23):
``PerplexityDirectAdapter``, the direct REST transport behind
``poseidon.mcp.registry.ToolServerRegistry``.

All calls in this file go through an injected fake ``httpx`` client
(``_FakeClient``/``_FakeResponse`` below) -- no network, matching
``test_llm_bedrock.py``'s own ``_FakeClient`` (records every call, replays
one canned response or raises one canned exception), the precedent this
file's fakes are modeled on. The one exception is
``test_search_live_smoke`` at the bottom, gated behind the
``research_live`` marker and a ``PERPLEXITY_API_KEY`` skip-guard, mirroring
``test_llm_bedrock.py``'s ``router_live`` smoke.

Fixtures (``poseidon/mcp/perplexity/fixtures/*.json``) are full recorded
Perplexity chat-completion envelopes -- ``choices[0].message.content`` is
itself a JSON-encoded STRING (the actual structured research payload),
exactly the real wire shape: the adapter parses that inner string
separately from the outer envelope. The three "truncated_*" fixtures and
"unrecoverable" were authored by slicing a known-good content string at
specific, deliberately chosen structural landmarks (see
``repair_truncated_json``'s own module docstring) and verifying by hand,
at authoring time, that the first three recover and the fourth does not --
these are not arbitrary truncations.

Non-ASCII: none needed (every pinned string here is plain ASCII already),
but ``test_perplexity_package_files_are_ascii_on_disk`` still scans this
file, ``poseidon/mcp/perplexity/__init__.py``, ``adapter.py``, and every
fixture/schema JSON file byte-for-byte, matching the convention
``test_mcp_module_files_are_ascii_on_disk`` (Task 1) and
``test_llm_module_files_are_ascii_on_disk`` /
``test_llm_bedrock_module_files_are_ascii_on_disk`` (Phase 5) each
established for their own new files.
"""

import json
import os
from pathlib import Path

import httpx
import pytest

from poseidon.mcp import perplexity
from poseidon.mcp.perplexity import adapter
from poseidon.mcp.perplexity.adapter import (
    RECENCY_FILTERS,
    SYSTEM_PROMPT,
    PerplexityDirectAdapter,
    load_schema,
    parse_with_recovery,
    repair_truncated_json,
    validate_and_normalize,
)
from poseidon.mcp.registry import ResearchResult

_FIXTURES_DIR = Path(adapter.__file__).resolve().parent / "fixtures"
_SCHEMAS_DIR = Path(adapter.__file__).resolve().parent / "schemas"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# offline test double -- records every post() call, replays one canned
# response (or raises one canned exception). Mirrors test_llm_bedrock.py's
# _FakeClient, standing in for a real httpx.Client via
# PerplexityDirectAdapter(client=...).
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self.calls: list[dict] = []
        self._response = response
        self._raises = raises

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._response


def _adapter_for(
    fixture_name: str, *, status_code: int = 200
) -> tuple[PerplexityDirectAdapter, _FakeClient]:
    fake = _FakeClient(response=_FakeResponse(status_code, _fixture(fixture_name)))
    return PerplexityDirectAdapter(api_key="test-key", client=fake), fake


# ---------------------------------------------------------------------------
# repair_truncated_json -- pure function, its own unit tests beyond the
# fixture cases (per the Task 2 brief).
# ---------------------------------------------------------------------------


def test_repair_truncated_json_closes_an_unterminated_string_then_the_open_braces():
    repaired = repair_truncated_json('{"title": "Hello wor')

    assert repaired == '{"title": "Hello wor"}'
    assert json.loads(repaired) == {"title": "Hello wor"}


def test_repair_truncated_json_closes_brackets_innermost_first():
    # Cut right after a complete array element closes, two levels open
    # (object, then array) -- the array (innermost) must close before the
    # object does, i.e. "]" is appended before "}".
    repaired = repair_truncated_json('{"items": [{"a": 1}')

    assert repaired == '{"items": [{"a": 1}]}'
    assert json.loads(repaired) == {"items": [{"a": 1}]}


def test_repair_truncated_json_handles_an_escaped_quote_inside_the_open_string():
    # The string is NOT terminated by the escaped quote -- only a real,
    # unescaped quote ends it. Cutting right after \" must still be
    # treated as "inside a string".
    repaired = repair_truncated_json(r'{"title": "she said \"hi')

    assert repaired == r'{"title": "she said \"hi"}'
    assert json.loads(repaired) == {"title": 'she said "hi'}


def test_repair_truncated_json_leaves_already_balanced_text_unchanged():
    text = '{"items": [1, 2, 3], "summary": "done"}'

    assert repair_truncated_json(text) == text


def test_repair_truncated_json_cannot_invent_a_missing_value():
    # Cut right after a key's colon -- closing brackets alone cannot
    # repair a missing value; this documents WHY the result can still
    # fail to parse (see the "unrecoverable" fixture test below).
    repaired = repair_truncated_json('{"a": 1, "b": ')

    assert repaired == '{"a": 1, "b": }'
    with pytest.raises(json.JSONDecodeError):
        json.loads(repaired)


def test_repair_truncated_json_trims_a_dangling_trailing_backslash():
    # Fix round 1, Minor M1 (reviewer-found adversarial case): truncation
    # landing exactly ON a backslash left `escape` pending forever -- the
    # naively-appended closing quote was consumed AS the escaped character
    # instead of terminating the string, so the "repaired" text was still
    # invalid JSON. The dangling backslash must be trimmed first.
    repaired = repair_truncated_json('{"title": "abc\\')

    assert repaired == '{"title": "abc"}'
    assert json.loads(repaired) == {"title": "abc"}


def test_repair_truncated_json_trims_an_incomplete_unicode_escape():
    # Fix round 1, Minor M1 (reviewer-found adversarial case): truncation
    # mid-way through a \uXXXX escape (here, only 2 of the required 4 hex
    # digits present) left the incomplete escape sitting right before the
    # appended closing quote, which the JSON parser then reads as part of
    # a still-incomplete \uXXXX rather than a real terminator. The whole
    # incomplete escape must be trimmed first.
    repaired = repair_truncated_json('{"a": "x\\u00')

    assert repaired == '{"a": "x"}'
    assert json.loads(repaired) == {"a": "x"}


# ---------------------------------------------------------------------------
# parse_with_recovery / validate_and_normalize -- the pipeline
# repair_truncated_json feeds into.
# ---------------------------------------------------------------------------


def test_parse_with_recovery_returns_none_when_recovery_still_fails():
    assert parse_with_recovery('{"a": 1, "b": ') is None


def test_parse_with_recovery_recovers_a_truncated_string():
    assert parse_with_recovery('{"title": "Hello wor') == {"title": "Hello wor"}


def test_validate_and_normalize_rejects_a_response_missing_the_items_key():
    schema = load_schema("web_research")

    assert validate_and_normalize({"summary": "no items field"}, schema) is None


def test_validate_and_normalize_rejects_a_non_dict_top_level_value():
    schema = load_schema("web_research")

    assert validate_and_normalize(["not", "a", "dict"], schema) is None


def test_validate_and_normalize_defaults_missing_item_fields_to_empty_string():
    schema = load_schema("web_research")

    items, summary = validate_and_normalize({"items": [{"title": "T"}]}, schema)

    assert items == ({"title": "T", "url": "", "snippet": "", "relevance": ""},)
    assert summary == ""


def test_validate_and_normalize_drops_non_dict_items_defensively():
    schema = load_schema("web_research")

    items, summary = validate_and_normalize(
        {"items": [{"title": "T", "url": "u", "snippet": "s", "relevance": "r"}, "not-a-dict"]},
        schema,
    )

    assert items == ({"title": "T", "url": "u", "snippet": "s", "relevance": "r"},)
    assert summary == ""


def test_validate_and_normalize_extracts_the_summary_field_when_present():
    # Task 4 (amendment 9a5ca1b): summary now threads through to
    # ResearchResult -- this is the normalize-layer proof that the text
    # actually gets pulled out of the parsed response, independent of
    # either transport's own search() plumbing.
    schema = load_schema("web_research")

    items, summary = validate_and_normalize(
        {"items": [], "summary": "Marine fuel prices held steady this week."}, schema
    )

    assert items == ()
    assert summary == "Marine fuel prices held steady this week."


def test_validate_and_normalize_defaults_summary_to_empty_string_when_absent():
    schema = load_schema("web_research")

    items, summary = validate_and_normalize({"items": [{"title": "T"}]}, schema)

    assert items == ({"title": "T", "url": "", "snippet": "", "relevance": ""},)
    assert summary == ""


# ---------------------------------------------------------------------------
# schema file -- authored fully by this task, versioned by filename.
# ---------------------------------------------------------------------------


def test_web_research_schema_has_the_pinned_v1_shape():
    schema = json.loads((_SCHEMAS_DIR / "web_research.json").read_text(encoding="utf-8"))

    assert schema["type"] == "object"
    assert schema["required"] == ["items"]
    item_schema = schema["properties"]["items"]["items"]
    assert item_schema["required"] == ["title", "url", "snippet", "relevance"]
    assert set(item_schema["properties"]) == {"title", "url", "snippet", "relevance"}
    assert schema["properties"]["summary"]["type"] == "string"


# ---------------------------------------------------------------------------
# lazy client -- mirrors BedrockProvider's client=None / _client_or_build.
# ---------------------------------------------------------------------------


def test_construction_does_not_build_a_real_httpx_client(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("httpx.Client() must not be constructed eagerly")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    PerplexityDirectAdapter(api_key="k")  # must not raise


def test_search_with_an_injected_client_never_constructs_a_real_httpx_client(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("httpx.Client() must not be constructed when client= is given")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)
    instance, _ = _adapter_for("clean")

    instance.search(query="marine biofuel Singapore", schema_name="web_research")  # must not raise


def test_client_or_build_lazily_constructs_and_caches_a_real_client():
    instance = PerplexityDirectAdapter(api_key="k")

    client = instance._client_or_build()

    assert isinstance(client, httpx.Client)
    assert instance._client_or_build() is client  # cached -- built once


# ---------------------------------------------------------------------------
# request shape -- pinned per the Task 2 brief.
# ---------------------------------------------------------------------------


def test_search_posts_the_pinned_request_shape():
    instance, fake = _adapter_for("clean")

    instance.search(query="marine biofuel Singapore", schema_name="web_research")

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://api.perplexity.ai/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    body = call["json"]
    assert body["model"] == "sonar"
    assert body["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "marine biofuel Singapore"},
    ]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "web_research", "schema": load_schema("web_research")},
    }
    assert "search_recency_filter" not in body


def test_search_uses_the_configured_model_and_timeout():
    fake = _FakeClient(response=_FakeResponse(200, _fixture("clean")))
    instance = PerplexityDirectAdapter(api_key="k", model="sonar-pro", timeout_s=5.0, client=fake)

    instance.search(query="q", schema_name="web_research")

    call = fake.calls[0]
    assert call["json"]["model"] == "sonar-pro"
    assert call["timeout"] == 5.0


@pytest.mark.parametrize(
    ("recency_days", "expected_filter"),
    [(7, "week"), (30, "month"), (365, "year")],
)
def test_search_maps_recency_days_to_search_recency_filter(recency_days, expected_filter):
    instance, fake = _adapter_for("clean")

    instance.search(query="q", schema_name="web_research", recency_days=recency_days)

    assert fake.calls[0]["json"]["search_recency_filter"] == expected_filter


def test_search_omits_recency_filter_when_none():
    instance, fake = _adapter_for("clean")

    instance.search(query="q", schema_name="web_research", recency_days=None)

    assert "search_recency_filter" not in fake.calls[0]["json"]


def test_search_omits_recency_filter_for_an_unmapped_value():
    # Only 7/30/365 are pinned (RECENCY_FILTERS); any other int is treated
    # like None rather than guessed at -- disclosed judgment call, no
    # brief text pins a behavior for this case.
    instance, fake = _adapter_for("clean")

    instance.search(query="q", schema_name="web_research", recency_days=14)

    assert "search_recency_filter" not in fake.calls[0]["json"]


def test_recency_filters_mapping_is_pinned_exactly():
    assert RECENCY_FILTERS == {7: "week", 30: "month", 365: "year"}


# ---------------------------------------------------------------------------
# fixtures, end to end through the adapter.
# ---------------------------------------------------------------------------


def test_search_returns_items_from_the_clean_fixture():
    instance, _ = _adapter_for("clean")

    result = instance.search(query="marine biofuel Singapore", schema_name="web_research")

    assert result == ResearchResult(
        items=(
            {
                "title": "Maersk expands biofuel bunkering in Singapore",
                "url": "https://example.com/maersk-biofuel-singapore",
                "snippet": (
                    "Maersk announced an expansion of its biofuel bunkering "
                    "program at the Port of Singapore."
                ),
                "relevance": "Directly relevant to marine biofuel adoption trends in the region.",
            },
            {
                "title": "IMO 2030 targets reshape bunker fuel demand",
                "url": "https://example.com/imo-2030-bunker-demand",
                "snippet": (
                    "New IMO greenhouse gas targets for 2030 are expected to shift "
                    "demand toward low-carbon marine fuels."
                ),
                "relevance": (
                    "Provides regulatory context for shipping-services fuel transition planning."
                ),
            },
        ),
        raw_digest="2 results via direct",
        transport="direct",
        degraded=False,
        degrade_reason=None,
        # Task 4 (amendment 9a5ca1b): the clean fixture's own recorded
        # content carries a "summary" key -- see fixtures/clean.json -- and
        # it must now thread all the way through to the result.
        summary=(
            "Recent coverage highlights growing biofuel bunkering capacity in "
            "Singapore and regulatory pressure from IMO 2030 targets pushing "
            "carriers toward lower-carbon marine fuels."
        ),
    )


@pytest.mark.parametrize(
    "fixture_name",
    ["truncated_mid_string", "truncated_mid_array", "truncated_mid_object"],
)
def test_search_recovers_each_truncated_fixture(fixture_name):
    instance, _ = _adapter_for(fixture_name)

    result = instance.search(query="marine biofuel Singapore", schema_name="web_research")

    assert result.degraded is False
    assert result.degrade_reason is None
    assert result.transport == "direct"
    assert len(result.items) >= 1
    assert result.raw_digest == f"{len(result.items)} results via direct"
    # Every recovered item is fully normalized -- the four schema keys,
    # nothing else, regardless of how deep the truncation cut into it.
    for item in result.items:
        assert set(item) == {"title", "url", "snippet", "relevance"}
    # Every one of the three truncation landmarks lands strictly before the
    # fixture's own "summary" key (see fixtures/*.json) -- repair_truncated_
    # json closes brackets/strings, it never invents a key that was never
    # written, so the recovered JSON has no "summary" at all and this
    # defaults to "" the same way an ordinary absent-field would.
    assert result.summary == ""


def test_search_degrades_on_the_unrecoverable_fixture():
    instance, _ = _adapter_for("unrecoverable")

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via direct",
        transport="direct",
        degraded=True,
        degrade_reason="could not parse perplexity response",
    )


def test_search_degrades_on_http_500():
    instance, _ = _adapter_for("http_500", status_code=500)

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via direct",
        transport="direct",
        degraded=True,
        degrade_reason="perplexity http 500",
    )


def test_search_degrades_on_timeout():
    fake = _FakeClient(raises=httpx.TimeoutException("timed out"))
    instance = PerplexityDirectAdapter(api_key="k", client=fake)

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via direct",
        transport="direct",
        degraded=True,
        degrade_reason="perplexity request timed out",
    )


def test_search_degrades_when_response_is_missing_the_items_key():
    # Not one of the brief's named fixtures -- a hand-built envelope
    # exercising validate_and_normalize's top-level gate end to end
    # through search(), disclosed as an addition beyond the literal
    # fixture list (same spirit as repair_truncated_json's own unit
    # tests: thoroughness on a defensive branch no fixture happens to
    # cover).
    payload = {"choices": [{"message": {"content": json.dumps({"summary": "no items field"})}}]}
    fake = _FakeClient(response=_FakeResponse(200, payload))
    instance = PerplexityDirectAdapter(api_key="k", client=fake)

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via direct",
        transport="direct",
        degraded=True,
        degrade_reason="perplexity response missing required fields",
    )


def test_search_never_raises_for_any_of_the_three_pinned_failure_modes():
    # A single cross-cutting proof, in addition to the per-case tests
    # above: none of the three pinned degrade paths ever lets an
    # exception escape search() itself.
    cases = [
        PerplexityDirectAdapter(
            api_key="k", client=_FakeClient(raises=httpx.TimeoutException("t"))
        ),
        PerplexityDirectAdapter(
            api_key="k", client=_FakeClient(response=_FakeResponse(500, _fixture("http_500")))
        ),
        PerplexityDirectAdapter(
            api_key="k", client=_FakeClient(response=_FakeResponse(200, _fixture("unrecoverable")))
        ),
    ]
    for instance in cases:
        result = instance.search(query="q", schema_name="web_research")
        assert result.degraded is True


# ---------------------------------------------------------------------------
# malformed-but-2xx envelopes -- fix round 1, Critical C1. A 2xx response
# whose body doesn't have the expected choices[0].message.content shape
# must degrade, never crash search() with an uncaught KeyError/IndexError/
# TypeError. The first four cases are the reviewer's own reproductions
# (empty choices list -> IndexError; missing choices/message/content keys
# -> KeyError); the fifth (message not a dict -> TypeError) is this round's
# own addition, since the fix catches TypeError too and no reviewer-named
# case exercised that branch specifically.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "envelope",
    [
        pytest.param({"choices": []}, id="empty-choices-list"),
        pytest.param({}, id="missing-choices-key"),
        pytest.param({"choices": [{}]}, id="missing-message-key"),
        pytest.param({"choices": [{"message": {}}]}, id="missing-content-key"),
        pytest.param({"choices": [{"message": "not-a-dict"}]}, id="message-not-a-dict"),
    ],
)
def test_search_degrades_on_a_malformed_but_2xx_envelope(envelope):
    fake = _FakeClient(response=_FakeResponse(200, envelope))
    instance = PerplexityDirectAdapter(api_key="k", client=fake)

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via direct",
        transport="direct",
        degraded=True,
        degrade_reason="malformed response envelope",
    )


# ---------------------------------------------------------------------------
# research_live -- credential-gated smoke (skips on this machine: no
# PERPLEXITY_API_KEY in the ambient environment). No Perplexity key exists
# tonight (doc: 2026-07-29-phase-7-research-mcp plan, "no Perplexity key" /
# Carlos's needs-keys item 2) -- recorded fixtures cover everything else in
# this file.
# ---------------------------------------------------------------------------

_NO_API_KEY_REASON = "no Perplexity API key"
_HAS_PERPLEXITY_API_KEY = bool(os.environ.get("PERPLEXITY_API_KEY"))


@pytest.mark.research_live
@pytest.mark.skipif(not _HAS_PERPLEXITY_API_KEY, reason=_NO_API_KEY_REASON)
def test_search_live_smoke():
    """Real Perplexity call -- shape only, never exact content (a live
    model's wording is not this test's business, mirroring
    test_llm_bedrock.py's own router_live smoke)."""
    instance = PerplexityDirectAdapter(api_key=os.environ["PERPLEXITY_API_KEY"])

    result = instance.search(
        query="Any recent marine biofuel bunkering news in Singapore?",
        schema_name="web_research",
        recency_days=30,
    )

    assert isinstance(result, ResearchResult)
    assert result.transport == "direct"
    assert isinstance(result.items, tuple)
    assert isinstance(result.degraded, bool)
    if not result.degraded:
        for item in result.items:
            assert set(item) == {"title", "url", "snippet", "relevance"}


# ---------------------------------------------------------------------------
# ASCII-only source and data files, matching the Phase 5 / Task 1 convention.
# ---------------------------------------------------------------------------


def test_perplexity_package_files_are_ascii_on_disk():
    """Byte-pinned degrade reasons and the pinned request shape stay
    pinned only if no look-alike codepoint can slip into any of these
    files -- see the module docstring. Extends the .py-only convention
    (test_mcp_module_files_are_ascii_on_disk et al.) to the fixture and
    schema JSON files too, since this task's house rule calls for "plain
    ASCII content preferred" there as well."""
    package_dir = Path(perplexity.__file__).parent
    paths = [
        package_dir / "__init__.py",
        Path(adapter.__file__),
        Path(__file__),
        *sorted(_FIXTURES_DIR.glob("*.json")),
        *sorted(_SCHEMAS_DIR.glob("*.json")),
    ]
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"

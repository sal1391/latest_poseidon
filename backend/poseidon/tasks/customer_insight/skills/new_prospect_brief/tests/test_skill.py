"""Tests for ``customer_insight.new_prospect_brief`` co-located with the
skill itself (folder law) -- ``Args``/``SKILL_META`` and ``skill.py``'s own
small helper functions, unit-tested directly.

See ``existing_customer_brief/tests/test_skill.py``'s own module docstring
for why the BIG cross-cutting scenarios live in
``backend/tests/test_brief_skills.py`` instead (identical reasoning here).
"""

import datetime as dt
from pathlib import Path

import pytest
from pydantic import ValidationError

from poseidon.tasks.customer_insight.skills.new_prospect_brief import schema, skill
from poseidon.tasks.customer_insight.skills.new_prospect_brief.schema import SKILL_META, Args

# ---------------------------------------------------------------------------
# Args -- prospect_name required, recency_days defaults to 365 and must be
# >= 1 (accepted for symmetry with existing_customer_brief; unused here --
# see skill.py's module docstring).
# ---------------------------------------------------------------------------


def test_args_requires_prospect_name():
    with pytest.raises(ValidationError):
        Args()


def test_args_prospect_name_alone_is_sufficient():
    args = Args(prospect_name="Meridian Global Shipping")

    assert args.prospect_name == "Meridian Global Shipping"
    assert args.recency_days == 365


def test_args_rejects_blank_prospect_name():
    with pytest.raises(ValidationError):
        Args(prospect_name="")


def test_args_rejects_a_non_positive_recency_days():
    with pytest.raises(ValidationError):
        Args(prospect_name="Meridian Global Shipping", recency_days=0)


# ---------------------------------------------------------------------------
# SKILL_META
# ---------------------------------------------------------------------------


def test_skill_meta_description_is_non_blank_and_under_the_cap():
    assert SKILL_META["description"].strip()
    assert len(SKILL_META["description"]) <= 300


def test_skill_meta_description_names_the_prospect_flow():
    description = SKILL_META["description"].lower()
    assert "prospect" in description


def test_skill_meta_has_at_least_one_example():
    assert SKILL_META["examples"]


# ---------------------------------------------------------------------------
# skill.py's own private helpers, unit-tested directly.
# ---------------------------------------------------------------------------


def test_phase_proof_lists_completed_and_failed_by_name_in_doc_order():
    proof = skill._phase_proof({"research": True, "contextualize": False, "strategize": False})

    assert proof == [
        "Phases completed: contextualize, strategize",
        "Phases failed: research",
    ]


def test_phase_proof_all_completed_reports_none_failed():
    proof = skill._phase_proof({"research": False, "contextualize": False, "strategize": False})

    assert proof[1] == "Phases failed: none"


def test_transport_name_is_none_when_ctx_tools_is_absent():
    ctx = _bare_ctx(tools=None)

    assert skill._transport_name(ctx) == "none"


def test_transport_name_reads_the_resolved_research_tools_class_name():
    class _FakeResearchTool:
        def search(self, *, query, schema_name, recency_days=None):  # pragma: no cover
            raise AssertionError("must not be called just to name the transport")

    class _Tools:
        research = _FakeResearchTool()

    ctx = _bare_ctx(tools=_Tools())

    assert skill._transport_name(ctx) == "_FakeResearchTool"


def test_render_markdown_renders_one_heading_per_phase_section_part():
    from poseidon.core.skills.result import phase_section_part

    parts = [
        phase_section_part("Operational Profile", "op text"),
        phase_section_part("Web Research", "web text"),
    ]

    body = skill._render_markdown("Meridian Global Shipping", parts)

    assert body.startswith("# Meridian Global Shipping Brief (Prospect)")
    assert "## Operational Profile" in body
    assert "op text" in body
    assert "## Web Research" in body
    assert "web text" in body


def test_today_returns_a_real_date_and_is_monkeypatchable(monkeypatch):
    assert isinstance(skill._today(), dt.date)

    monkeypatch.setattr(skill, "_today", lambda: dt.date(2026, 7, 1))

    assert skill._today() == dt.date(2026, 7, 1)


# ---------------------------------------------------------------------------
# ASCII guard
# ---------------------------------------------------------------------------


def test_new_skill_files_are_ascii_on_disk():
    package_dir = Path(skill.__file__).parent
    for path in [
        Path(schema.__file__),
        Path(skill.__file__),
        Path(__file__),
        package_dir / "__init__.py",
        package_dir / "tests" / "__init__.py",
    ]:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bare_ctx(*, tools):
    from poseidon.core.config import Settings
    from poseidon.core.skills.context import SkillContext

    return SkillContext(
        data=object(),
        artifacts=None,
        settings=Settings(
            _env_file=None,
            database_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/void",
            s3_bucket="poseidon-artifacts",
        ),
        tools=tools,
    )

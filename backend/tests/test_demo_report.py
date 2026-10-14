import pytest

from poseidon.core.skills.context import ArtifactRef
from poseidon.scripts import demo_report
from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
)


def test_parse_args_requires_a_yyyy_mm_month():
    args = demo_report.parse_args(["--perspective", "book", "--month", "2026-06", "--out", "x.pdf"])
    assert args.month == "2026-06" and args.office is None
    with pytest.raises(SystemExit):
        demo_report.parse_args(["--perspective", "book", "--month", "2026-6", "--out", "x.pdf"])


def test_render_summary_shows_tiles_footers_movers_and_flags():
    payload = minimal_payload()
    artifact = ArtifactRef("x.pdf", "/api/reports/1/pdf", "application/pdf")
    text = demo_report.render_summary(payload, [artifact])
    assert payload["scope"]["label"] in text
    assert payload["kpis"]["GP"]["display"]["current"] in text
    assert "entities_by_gp" in text and "footer == tile: yes" in text
    for mover in payload["movers"]["increases"]:
        assert mover["key"] in text and mover["driver"] in text
    for question in payload["suggested_questions"]:
        assert question in text
    assert text.isascii()


def test_main_without_database_url_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert demo_report.main(["--perspective", "book", "--month", "2026-06", "--out", "x.pdf"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err

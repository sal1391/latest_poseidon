import re

import pytest

from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import render

try:
    import weasyprint  # noqa: F401 - existence probe only
except Exception:  # noqa: BLE001 - missing native libs raise OSError, not ImportError
    _HAS_WEASYPRINT = False
else:
    _HAS_WEASYPRINT = True


def test_html_carries_every_section_in_order():
    html = render.render_html(minimal_payload())
    order = [
        "Monthly performance report",
        'class="kpis"',
        "Customers by GP",
        "Customers by volume",
        "Ports by GP",
        "Ports by volume",
        "Deal class",
        "Top increases",
        "Top decreases",
        "New in",
        "Lost from",
        "Summary of operations",
        "Suggested questions",
        "Proof",
    ]
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_every_table_footer_shows_the_kpi_display_strings():
    payload = minimal_payload()
    html = render.render_html(payload)
    gp = payload["kpis"]["GP"]["display"]
    assert html.count("<tfoot>") == 5
    assert gp["current"] in html and gp["prior"] in html


def test_month_to_date_is_named_in_the_header():
    payload = minimal_payload()
    payload["periods"]["window_kind"] = "mtd"
    payload["periods"]["current"]["label"] = "October 1 to 11, 2026 (month to date)"
    html = render.render_html(payload)
    assert "October 1 to 11, 2026 (month to date)" in html
    assert "Month to date" in html
    assert "Month to date" not in render.render_html(minimal_payload())


def test_hostile_entity_name_renders_as_text_and_fetches_nothing(monkeypatch):
    """Review Focus 2 and reports design 5.7."""
    payload = minimal_payload()
    hostile = '<img src="http://evil.example/x.png"> {{ 7*7 }}'
    payload["tables"]["entities_by_gp"]["rows"][0]["key"] = hostile
    payload["narrative"]["template_md"] = f"**{hostile}** led the month with 1,000."
    html = render.render_html(payload)
    assert "<img" not in html
    assert "&lt;img" in html
    assert "49" not in html and "{{" not in html

    captured = {}

    class _FakeHTML:
        def __init__(self, *, string, url_fetcher):
            captured["url_fetcher"] = url_fetcher

        def write_pdf(self):
            return b"%PDF-fake"

    import sys
    import types

    monkeypatch.setitem(sys.modules, "weasyprint", types.SimpleNamespace(HTML=_FakeHTML))
    assert render.render_pdf(html) == b"%PDF-fake"
    with pytest.raises(RuntimeError, match="refuses to fetch"):
        captured["url_fetcher"]("http://evil.example/x.png")


def test_outlook_safety_lint():
    html = render.render_html(minimal_payload())
    assert "<script" not in html
    assert "<link" not in html
    assert not re.search(r"position\s*:", html)
    assert not re.search(r"display\s*:\s*(flex|grid)", html)
    assert html.count("<style>") == 1


def test_withheld_flag_and_template_fallback_are_shown():
    payload = minimal_payload()
    payload["narrative"]["llm_md"] = "Model prose that must not show."
    payload["narrative"]["grounded"] = False
    payload["narrative"]["flags"] = ["AI narrative withheld: 3 unverified figures"]
    html = render.render_html(payload)
    assert "Model prose that must not show." not in html
    assert "AI narrative withheld: 3 unverified figures" in html
    assert payload["narrative"]["template_md"].split("\n")[0].strip("*# ") in html


def test_strict_undefined_rejects_a_payload_missing_a_section():
    payload = minimal_payload()
    del payload["suggested_questions"]
    with pytest.raises(Exception, match="suggested_questions"):
        render.render_html(payload)


@pytest.mark.pdf
@pytest.mark.skipif(
    not _HAS_WEASYPRINT, reason="weasyprint needs Pango/Cairo - run in the container"
)
def test_real_pdf_bytes():
    pdf = render.render_pdf(render.render_html(minimal_payload()))
    assert pdf.startswith(b"%PDF")

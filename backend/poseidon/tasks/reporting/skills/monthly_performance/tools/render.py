"""Render the report payload (v1) to HTML, and HTML to PDF.

Every data string is autoescaped. The narrative is escaped first and only then run
through markdown, so a hostile entity name can never become markup while a
``**bold**`` heading still renders. The PDF renderer can never reach the network. Jinja
delimiters are entity-encoded in the final HTML so no template syntax reaches the stored document.
"""

from __future__ import annotations

import html as _html
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

import poseidon

TEMPLATES_DIR = Path(poseidon.__file__).resolve().parent / "config" / "templates"


def narrative_html(markdown_text: str) -> Markup:
    """Escape, then markdown: the text can be bold or a list but never raw markup."""
    return Markup(markdown.markdown(_html.escape(markdown_text), output_format="html5"))


_ENV = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=StrictUndefined,
    auto_reload=False,
)
_ENV.filters["narrative_html"] = narrative_html


def render_html(payload: Mapping[str, Any]) -> str:
    out = _ENV.get_template("report.html.j2").render(**payload)
    # The delimiters are entity-encoded after rendering because the test contract (reports
    # design 5.7 / Review Focus 2) requires that no Jinja delimiter reaches the stored HTML,
    # which also keeps the stored document inert if a later pass (Phase 18 email) ever runs
    # it through another template engine. The <style> block is untouched: CSS has only
    # single braces, never "{{", "{%" or "{#".
    return out.replace("{{", "&#123;&#123;").replace("{%", "&#123;%").replace("{#", "&#123;#")


def refuse_fetch(url: str, *args: object, **kwargs: object) -> NoReturn:
    raise RuntimeError(f"report renderer refuses to fetch {url}")


def render_pdf(html: str) -> bytes:
    from weasyprint import HTML  # lazy: needs native Pango/Cairo

    return HTML(string=html, url_fetcher=refuse_fetch).write_pdf()

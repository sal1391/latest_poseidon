"""``render_brief`` -- render a brief's markdown body to HTML and a PDF.

The last of the three deterministic tools doc 02 section 4 lists for the
``existing_customer_brief`` skill (see ``fetch_metrics.py``'s module
docstring for why these tools are built and unit-tested here): markdown text
in, a :class:`RenderedBrief` (HTML always, PDF bytes when WeasyPrint can
render) out. Storing it is ``persist_brief``'s job, not this module's.

Pipeline: ``markdown_body`` -> HTML (the ``markdown`` library) -> wrapped in a
minimal print stylesheet -> WeasyPrint renders that HTML to PDF bytes.

**WeasyPrint is imported inside the function, not at module level.**
WeasyPrint links Pango/Cairo/GDK-Pixbuf at import time and raises ``OSError``
(not ``ImportError``) when they are missing -- true of every host without the
native libraries the Dockerfile installs (see ``infra/backend.Dockerfile.dev``
and the ``pdf`` marker's ``_HAS_WEASYPRINT`` probe in this skill's
``tests/test_tools.py``). Importing it lazily keeps this module itself
host-safe to import. ``render_brief`` never raises: any render failure is
reported as ``pdf=None`` plus the exception class name in ``pdf_error``, and
the HTML is still returned.

**Proof lines never hash the PDF.** WeasyPrint embeds a creation timestamp in
every PDF it writes, so byte-for-byte output is not deterministic even for
identical input. Proof is the brief id, the page count, and the byte size
instead; tests assert the ``%PDF`` magic and a positive page count, never a
fixed hash.
"""

from dataclasses import dataclass
from html import escape

import markdown

# Deliberately minimal: this is a print stylesheet for an internal sales
# brief, not a marketing document. Enough to make headings, body text, and
# any metric/port table legible on a printed A4 page — nothing decorative.
_STYLESHEET = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 11pt;
       line-height: 1.4; color: #111; }
h1 { font-size: 18pt; margin: 0 0 4mm 0; }
h2 { font-size: 14pt; margin-top: 6mm; }
h3 { font-size: 12pt; margin-top: 5mm; }
table { border-collapse: collapse; width: 100%; margin: 4mm 0; }
th, td { border: 1px solid #999; padding: 1mm 2mm; text-align: left; }
th { background: #eee; }
"""

def _render_html(title: str, markdown_body: str) -> str:
    """The full HTML document WeasyPrint renders — pure string building, no
    WeasyPrint import, so this half of the pipeline is host-testable without
    Pango/Cairo installed.

    ``body_html`` is inserted unescaped, deliberately: WeasyPrint has no
    script engine to exploit (there is no XSS-equivalent for a PDF renderer),
    and the content is markdown authored by the brief pipeline (deterministic
    tool output today, subskill/LLM synthesis in later phases) rather than
    raw HTML from an untrusted browser input. ``title`` is escaped because it
    is interpolated directly, without going through the markdown converter.
    """
    body_html = markdown.markdown(markdown_body, extensions=["tables", "sane_lists"])
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<style>{_STYLESHEET}</style></head>"
        f"<body><h1>{escape(title)}</h1>{body_html}</body></html>"
    )


@dataclass(frozen=True)
class RenderedBrief:
    html: str
    pdf: bytes | None
    pdf_error: str | None  # exception class name when pdf is None
    page_count: int | None


def render_brief(title: str, markdown_body: str) -> RenderedBrief:
    """Render ``markdown_body`` to HTML and, when WeasyPrint works, to PDF.

    Never raises: a missing native library or any render error yields
    ``pdf=None``, ``page_count=None`` and ``pdf_error`` set to the exception
    class name; the HTML is returned either way.
    """
    html = _render_html(title, markdown_body)
    try:
        from weasyprint import HTML  # see module docstring: lazy, host-safe import

        document = HTML(string=html).render()
        pdf = document.write_pdf()
        page_count = len(document.pages)
    except Exception as exc:  # noqa: BLE001 - OSError for missing Pango, anything else too
        return RenderedBrief(html=html, pdf=None, pdf_error=type(exc).__name__, page_count=None)
    return RenderedBrief(html=html, pdf=pdf, pdf_error=None, page_count=page_count)

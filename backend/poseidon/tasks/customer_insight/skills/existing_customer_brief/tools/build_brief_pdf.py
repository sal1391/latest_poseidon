"""``build_brief_pdf`` — render a brief's markdown body to a stored PDF.

The last of the three deterministic tools doc 02 §4 lists for the future
``existing_customer_brief`` skill (see ``fetch_metrics.py``'s module
docstring for why these tools are built and unit-tested here, ahead of the
Phase 8 skill that will call them in sequence): markdown text in, an
:class:`~poseidon.core.skills.context.ArtifactRef` to a PDF out.

Pipeline: ``markdown_body`` -> HTML (the ``markdown`` library) -> wrapped in a
minimal print stylesheet -> WeasyPrint renders that HTML to PDF bytes ->
:meth:`~poseidon.core.artifacts.ArtifactStore.put_pdf` uploads it under
``f"{key_prefix}/{slug(title)}.pdf"``.

**WeasyPrint is imported inside the function, not at module level.**
WeasyPrint links Pango/Cairo/GDK-Pixbuf at import time and raises ``OSError``
(not ``ImportError``) when they are missing — true of every host without the
native libraries the Dockerfile installs (see ``infra/backend.Dockerfile.dev``
and the ``pdf`` marker's ``_HAS_WEASYPRINT`` probe in this skill's
``tests/test_tools.py``). Importing it lazily keeps this module itself
host-safe to import; only *calling* ``build_brief_pdf`` requires the native
libraries.

**Proof lines never hash the PDF.** WeasyPrint embeds a creation timestamp in
every PDF it writes, so byte-for-byte output is not deterministic even for
identical input — hashing it would produce a "proof" that changes on every
run for no substantive reason. Proof is the artifact key, the page count, and
the byte size instead; tests assert the ``%PDF`` magic and a positive page
count, never a fixed hash.
"""

import re
from html import escape

import markdown

from poseidon.core.artifacts import ArtifactStore
from poseidon.core.skills.context import ArtifactRef

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

_SLUG_DISALLOWED = re.compile(r"[^a-z0-9]+")


def slug(title: str) -> str:
    """Lowercase ``title`` with every run of non-alphanumeric characters
    collapsed to a single hyphen, and no leading or trailing hyphen — e.g.
    ``"Northstar Lines: Q3 Brief!"`` -> ``"northstar-lines-q3-brief"``.

    Falls back to ``"untitled"`` when that leaves nothing — an empty,
    whitespace-only, or punctuation-only ``title``, or one written entirely
    in a non-Latin script (``_SLUG_DISALLOWED`` only keeps ASCII
    ``[a-z0-9]``, so e.g. ``"日本語"`` has no alphanumeric characters by that
    definition), would otherwise collapse to the empty string and every such
    title would silently collide on the same key, ``f"{key_prefix}/.pdf"``,
    overwriting one another with no error.

    Used to turn an arbitrary brief title into a safe, readable object key
    component; never claimed to be reversible or collision-free beyond that
    one fallback (two titles differing only in punctuation can still produce
    the same slug, same as any typical slugify implementation) — avoiding
    *that* kind of collision is the caller's responsibility via
    ``key_prefix`` (the Phase-8 skill's keys carry customer and date).
    """
    slugged = _SLUG_DISALLOWED.sub("-", title.strip().lower()).strip("-")
    return slugged or "untitled"


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


def build_brief_pdf(
    store: ArtifactStore, title: str, markdown_body: str, key_prefix: str
) -> tuple[ArtifactRef, list[str]]:
    """Render ``markdown_body`` to PDF and upload it to ``store``.

    Returns ``(ref, proof_lines)``: ``ref`` is the uploaded artifact's
    presigned reference, and ``proof_lines`` is exactly three lines — the
    object key, the rendered page count, and the byte size — in that order.
    """
    from weasyprint import HTML  # see module docstring: lazy, host-safe import

    document = HTML(string=_render_html(title, markdown_body)).render()
    content = document.write_pdf()
    page_count = len(document.pages)

    key = f"{key_prefix}/{slug(title)}.pdf"
    ref = store.put_pdf(key, content)

    proof = [
        f"Artifact: {key}",
        f"Pages: {page_count}",
        f"Bytes: {len(content)}",
    ]
    return ref, proof

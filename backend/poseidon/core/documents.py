"""Helpers shared by the document-producing skills (reports and briefs)."""

from __future__ import annotations

import re

ANONYMOUS_SUB = "dev|anonymous"


def safe_filename(name: str) -> str:
    """Ruling 13: keep [A-Za-z0-9 ._-], replace the rest with _, collapse spaces."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "report"

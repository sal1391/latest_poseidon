"""Text normalization (doc 02 section 5): the first, always-run stage of the
deterministic parsing pipeline.
"""

import unicodedata


def normalize(text: str) -> str:
    """Unicode-NFC-normalize ``text`` and collapse whitespace.

    Strips leading/trailing whitespace and collapses every internal run of
    whitespace (spaces, tabs, newlines, ...) to a single space. Case is
    preserved -- later stages that need casefolding (the customer/port
    resolver) do it themselves, so the original text stays available
    verbatim for anything downstream that wants it (e.g. echoing a
    clarification back to the user).
    """
    composed = unicodedata.normalize("NFC", text)
    return " ".join(composed.split())

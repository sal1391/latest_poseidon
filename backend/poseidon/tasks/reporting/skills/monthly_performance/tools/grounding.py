"""D67: the entity-scoped grounding check.

Every number a narrative quotes must be one of the payload's own display strings,
and an entity's figures may only be quoted when that entity is in scope. The KPI
figures (tiles and table footers) are in scope for every sentence; an entity's
figures need the entity named in the sentence or earlier in the same paragraph.

Pipeline: ``build_index`` walks the payload once and records raw display strings
(the token spellings each display admits are derived at check time by
``_variants``). ``check`` splits the text into paragraphs and sentences, collects
the entity scope as it walks a paragraph, tokenises each sentence and reports the
tokens that match no display string in scope.

Pure Python, no I/O; this module never raises on odd input text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

_UNICODE_MINUS = "\u2212"

# A sign is either the word "minus" followed by whitespace, or one of + - U+2212
# immediately before the first digit. The "->" arrow's "-" is followed by ">", so
# it is never a sign. A digit run preceded by a word character or "." is not a
# token start (so "v2" and the fraction of "1.5" never yield extra tokens).
_SIGN = r"(?:minus\s+(?=\d)|[-+\u2212])?"
_TOKEN = re.compile(
    r"(?<![\w.])" + _SIGN + r"\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
    r"|(?<![\w.])" + _SIGN + r"\d+(?:\.\d+)?%?"
)
_MINUS_WORD = re.compile(r"^minus\s+")

_NON_NUMERIC = frozenset({"flat", "new", "n/a"})
_METRICS = ("GP", "VOLUME", "MARGIN")
_DISPLAY_FIELDS = ("prior", "current", "change", "pct")
_SUFFIXES = ("%", " pts")

_HEADING = re.compile(r"^#{1,6}\s+")
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_CONTINUATION_MARKER = re.compile(r"^\s+(?:[-*+]|\d+[.)])\s+")
# A sentence ends at . ! or ? (optionally followed by closing emphasis, brackets or
# quotes) when whitespace follows. Decimals ("29.54") never end a sentence.
_SENTENCE_END = re.compile(r"[.!?][*_)\]\"']*(?=\s)")


@dataclass(frozen=True)
class GroundingIndex:
    kpi: frozenset[str]  # display strings of the KPI tiles and every table footer
    by_entity: Mapping[str, frozenset[str]]  # canonical name -> display strings that belong to it
    aliases: Mapping[str, str]  # casefolded alias -> canonical name
    exempt: frozenset[str]  # the integers that appear in the two period labels


@dataclass(frozen=True)
class Violation:
    sentence: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class GroundingReport:
    ok: bool
    violations: tuple[Violation, ...]
    checked: int  # non-exempt tokens examined

    @property
    def unverified(self) -> int:
        return sum(len(v.tokens) for v in self.violations)


# --- Index ------------------------------------------------------------------------


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _displays(value: Any) -> Iterable[str]:
    """The string values of a display mapping, minus the non-numeric words."""
    for field in _DISPLAY_FIELDS:
        text = _mapping(value).get(field)
        if isinstance(text, str) and text not in _NON_NUMERIC:
            yield text


def _row_displays(row: Mapping[str, Any]) -> Iterable[str]:
    display = _mapping(row.get("display"))
    for metric in _METRICS:
        yield from _displays(display.get(metric))


def _keyed_rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Every entity-bearing row: table rows and Other rows, movers, new and lost."""
    for table in _mapping(payload.get("tables")).values():
        table = _mapping(table)
        yield from (r for r in table.get("rows") or () if isinstance(r, Mapping))
        if isinstance(table.get("other"), Mapping):
            yield table["other"]
    for group in ("movers", "new_lost"):
        for rows in _mapping(payload.get(group)).values():
            yield from (r for r in rows or () if isinstance(r, Mapping))


def _first_word_aliases(names: Iterable[str]) -> dict[str, str]:
    """casefolded first word -> name, when the word is 4+ letters and unshared."""
    by_word: dict[str, list[str]] = {}
    for name in names:
        words = name.casefold().split()
        if words:
            by_word.setdefault(words[0], []).append(name)
    return {
        word: owners[0]
        for word, owners in by_word.items()
        if len(owners) == 1 and len(word) >= 4 and word.isalpha()
    }


def build_index(payload: Mapping[str, Any]) -> GroundingIndex:
    kpi: set[str] = set()
    for delta in _mapping(payload.get("kpis")).values():
        kpi.update(_displays(_mapping(delta).get("display")))
    for table in _mapping(payload.get("tables")).values():
        footer = _mapping(_mapping(table).get("footer"))
        kpi.update(_row_displays(footer))

    by_entity: dict[str, set[str]] = {}

    def add(name: Any, texts: Iterable[str]) -> None:
        if isinstance(name, str) and name.strip():
            by_entity.setdefault(name, set()).update(
                t for t in texts if isinstance(t, str) and t not in _NON_NUMERIC
            )

    for row in _keyed_rows(payload):
        add(row.get("key"), _row_displays(row))

    for name, context in _mapping(payload.get("entity_context")).items():
        context = _mapping(context)
        book = _mapping(context.get("book"))
        add(name, (t for m in _METRICS for t in _displays(_mapping(book.get(m)).get("display"))))
        share = _mapping(_mapping(context.get("share_of_book")).get("display"))
        add(name, (share.get("prior"), share.get("current")))
        recon = _mapping(_mapping(context.get("reconciliation")).get("display"))
        add(name, (recon.get(k) for k in ("office_change", "book_change", "other_offices_net")))
        for group in ("offsets", "ports_rest_of_book"):
            for item in context.get(group) or ():
                item = _mapping(item)
                display = item.get("display")
                # Indexed twice: under the mover and under the named office or port.
                add(name, (display,))
                add(item.get("key"), (display,))

    aliases: dict[str, str] = _first_word_aliases(by_entity)
    # A full name always wins over another name's first-word alias.
    aliases.update({name.casefold(): name for name in by_entity})

    exempt: set[str] = set()
    for side in ("current", "prior"):
        label = _mapping(_mapping(payload.get("periods")).get(side)).get("label")
        if isinstance(label, str):
            exempt.update(str(int(n)) for n in re.findall(r"\d+", label))

    return GroundingIndex(
        kpi=frozenset(kpi),
        by_entity=MappingProxyType({k: frozenset(v) for k, v in by_entity.items()}),
        aliases=MappingProxyType(aliases),
        exempt=frozenset(exempt),
    )


# --- Tokens -----------------------------------------------------------------------


def numeric_tokens(sentence: str) -> list[str]:
    """Numeric tokens in order, normalised: U+2212 -> "-", "minus 7,700" -> "-7,700"."""
    out = []
    for match in _TOKEN.finditer(sentence or ""):
        token = _MINUS_WORD.sub("-", match.group(0)).replace(_UNICODE_MINUS, "-")
        out.append(token)
    return out


def _is_exempt(token: str, index: GroundingIndex) -> bool:
    if not token.isdigit():  # signed, separated, decimal or percent: never exempt
        return False
    value = int(token)
    if len(token) == 4 and 1900 <= value <= 2099:
        return True
    if value <= 10:
        return True
    return str(value) in index.exempt


def _variants(display: str) -> frozenset[str]:
    """The token spellings a display string admits.

    ``d`` itself and ``d`` without its leading sign; when ``d`` ends in ``%`` or
    `` pts``, the same two forms without that suffix. A ``$`` is dropped first,
    because the tokeniser never includes it in a token.
    """
    d = display.strip().replace("$", "")
    if d in _NON_NUMERIC or not d:
        return frozenset()
    forms = {d}
    for suffix in _SUFFIXES:
        if d.endswith(suffix):
            forms.add(d[: -len(suffix)])
    out = set(forms)
    for form in forms:
        if form[:1] in "+-":
            out.add(form[1:])
    return frozenset(out)


# --- Entities and structure ---------------------------------------------------------


def find_entities(sentence: str, index: GroundingIndex) -> list[str]:
    """Canonical names named in the sentence, in first-appearance order, no repeats."""
    folded = (sentence or "").casefold()
    first_seen: dict[str, int] = {}
    for alias, name in index.aliases.items():
        match = re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", folded)
        if match and (name not in first_seen or match.start() < first_seen[name]):
            first_seen[name] = match.start()
    return sorted(first_seen, key=lambda name: (first_seen[name], name))


def _sentences(paragraph: str) -> list[str]:
    out: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(paragraph):
        out.append(paragraph[start : match.end()])
        start = match.end()
    out.append(paragraph[start:])
    return [s.strip() for s in out if s.strip()]


def split_paragraphs(text: str) -> list[list[str]]:
    """Paragraphs, each a list of sentences.

    A paragraph ends at a blank line; a markdown heading line is its own paragraph;
    an unindented list item line (``- ``, ``* ``, ``1. ``) starts a new paragraph.
    An indented line (a nested bullet under a list item) continues the paragraph it
    sits under, so a mover's sub-bullets stay in that mover's scope. List markers
    and heading hashes are stripped so a list number is never read as a token.
    """
    blocks: list[list[str]] = []
    current: list[str] = []

    def close() -> None:
        if current:
            blocks.append(list(current))
            current.clear()

    for line in (text or "").splitlines():
        if not line.strip():
            close()
        elif _HEADING.match(line):
            close()
            blocks.append([_HEADING.sub("", line)])
        elif _LIST_ITEM.match(line):
            close()
            current.append(_LIST_ITEM.sub("", line))
        else:
            current.append(_CONTINUATION_MARKER.sub("", line).strip())
    close()

    paragraphs = []
    for block in blocks:
        sentences = _sentences(" ".join(part.strip() for part in block))
        if sentences:
            paragraphs.append(sentences)
    return paragraphs


# --- Verdict ------------------------------------------------------------------------


def check(text: str, index: GroundingIndex) -> GroundingReport:
    violations: list[Violation] = []
    checked = 0
    for paragraph in split_paragraphs(text):
        scope: list[str] = []
        for sentence in paragraph:
            for name in find_entities(sentence, index):
                if name not in scope:
                    scope.append(name)
            tokens = [t for t in numeric_tokens(sentence) if not _is_exempt(t, index)]
            if not tokens:
                continue
            checked += len(tokens)
            displays = set(index.kpi)
            for name in scope:
                displays |= index.by_entity.get(name, frozenset())
            allowed = frozenset().union(*(_variants(d) for d in displays))
            bad = tuple(t for t in tokens if t not in allowed)
            if bad:
                violations.append(Violation(sentence=sentence, tokens=bad))
    return GroundingReport(ok=not violations, violations=tuple(violations), checked=checked)

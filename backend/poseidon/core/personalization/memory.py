"""Phase 13 Task 1 (doc 05 section 5): the Postgres-backed store behind
migration 0008's ``user_memory`` table -- an append-only, versioned set of
typed, attributed memory entries per user (``current = max(version)``),
injected into every router/synthesis prompt (Task 2 wires the injection;
this task only builds the store).

**Shape mirrors ``core/chat/history.py``'s ``HistoryStore``/``UserHistory``
on purpose.** ``MemoryStore(engine, app_role).for_user(sub) -> UserMemory``
is the identical construction discipline: cheap to construct, no connection
opened until a :class:`UserMemory` method actually runs one, ``app_role``
threaded straight through to :func:`~poseidon.core.db.rls_transaction`
exactly like every other caller of that wrapper in this codebase.

**Explicit ``user_sub`` predicate on every query, unlike ``core/chat/
history.py``'s UUID-keyed tables.** ``user_memory``'s primary key is
(``user_sub``, ``version``) -- ``version`` alone is never globally unique
(every user's own numbering restarts at 1), so a lookup keyed only on
``version`` would, on any connection where RLS is for some reason not
being enforced, silently return some OTHER user's row at that version
number instead of failing closed. Every version-keyed query below adds an
explicit ``user_sub = :user_sub`` predicate, layered on top of (never a
replacement for) RLS itself -- see migration 0008's own docstring for the
full rationale, and ``core/chat/feedback.py``'s own ``_GET_SQL`` for the
precedent this mirrors.

**Memory entries are typed and attributed, never free text (doc 05 section
5).** :func:`_validate_entries` rejects, before any row is inserted, any
entry whose ``type`` is not one of the closed set (``preference``,
``scope``, ``fact``, ``correction``), or whose ``statement``/
``source_conversation_id``/``at`` is missing or blank. This module has no
way to check the DEEPER doc 05 section 5 constraint ("an entry is only
admissible if it derives from something the user said or a choice the user
confirmed... text returned by web-research or any other external tool must
never become an entry") -- that is a property of what the DISTILLER wrote,
enforced by prompt design and review at the point entries are authored
(Task 4), not something a stored entry's shape alone can prove or disprove.

**Size cap enforcement lives at exactly ONE point: :meth:`UserMemory.
write_version`.** It renders the candidate entries to markdown via
:func:`_render_entries_markdown` -- the SAME private function :meth:`~
UserMemory.render_markdown` calls at prompt-injection time, so the two can
never disagree about what "the rendered form" means -- and raises
:class:`MemoryTooLarge` if that render exceeds ``settings.memory_max_chars``,
before any row is inserted. :meth:`render_markdown` itself performs no
capping of its own: it trusts that anything already in the table already
passed this check at write time.

**Version retention is enforced at write time too, inside
:meth:`write_version`, AFTER the new version successfully inserts:** rows
older than the newest ``settings.memory_keep_versions`` for this user are
deleted in the same transaction. ``settings.memory_max_chars``/``settings.
memory_keep_versions`` are read fresh (``get_settings()``, itself
``lru_cache``-wrapped -- a cheap, already-memoized lookup) on every call
rather than captured at construction time, since :class:`MemoryStore` is
built once per process (``api/app.py``'s own wiring, mirroring
``history_store``/``feedback_store``) and a live deploy's configuration
should never require a restart to take effect for these two values.
"""

import json
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.config import get_settings
from poseidon.core.db import rls_transaction

#: doc 05 section 5's closed set -- "entries are typed and attributed, never
#: accumulated prose."
_ENTRY_TYPES = frozenset({"preference", "scope", "fact", "correction"})

#: The fields every entry must carry, verbatim from doc 05 section 5's own
#: schema comment: ``[{type, statement, source_conversation_id, at}]``.
_REQUIRED_ENTRY_FIELDS = ("type", "statement", "source_conversation_id", "at")


class MemoryValidationError(ValueError):
    """Raised by :meth:`UserMemory.write_version` when a candidate entry
    fails doc 05 section 5's own closed-set/required-field contract --
    checked, and raised, BEFORE any row is inserted. A :class:`ValueError`
    subclass, not a bare custom exception -- mirrors ``core/chat/
    history.py``'s ``MalformedCursor``/``core/chat/feedback.py``'s
    ``FeedbackNotApplicable`` precedent, so any pre-existing broad
    ``except ValueError`` a caller might already have keeps working
    unchanged."""


class MemoryTooLarge(ValueError):
    """Raised by :meth:`UserMemory.write_version` when the candidate
    entries' rendered markdown (:func:`_render_entries_markdown`) exceeds
    ``settings.memory_max_chars`` -- checked, and raised, BEFORE any row is
    inserted; never a silent truncation. Same typed-``ValueError``-subclass
    precedent as :class:`MemoryValidationError`."""


def _validate_entries(entries: list[dict]) -> None:
    """Raise :class:`MemoryValidationError` on the first entry that fails
    doc 05 section 5's contract -- every entry must carry all of
    :data:`_REQUIRED_ENTRY_FIELDS`, ``type`` must be one of
    :data:`_ENTRY_TYPES`, and ``statement``/``source_conversation_id``/
    ``at`` must each be non-blank. Runs entirely in Python, before any SQL
    is built or any connection opened -- the "before any row is inserted"
    half of the contract is structural, not merely tested."""
    for entry in entries:
        missing = [field for field in _REQUIRED_ENTRY_FIELDS if not entry.get(field)]
        if missing:
            raise MemoryValidationError(
                f"entry {entry!r} is missing required field(s): {missing}"
            )
        entry_type = entry["type"]
        if entry_type not in _ENTRY_TYPES:
            raise MemoryValidationError(
                f"entry type {entry_type!r} is not one of {sorted(_ENTRY_TYPES)}"
            )
        if not str(entry["statement"]).strip():
            raise MemoryValidationError(f"entry {entry!r} has a blank statement")


def _render_entries_markdown(entries: list[dict]) -> str:
    """The ONE rendered form of a memory document -- shared, verbatim, by
    both :meth:`UserMemory.write_version`'s size-cap check and
    :meth:`UserMemory.render_markdown`'s prompt-injection output (module
    docstring). ``""`` for an empty entry list -- the module docstring's
    "no section" behavior for a user with no memory at all is
    :meth:`render_markdown`'s job to fall back to, not this function's;
    this function only ever renders whatever list it is given."""
    lines = [
        f"- [{entry['type']}] {entry['statement']} "
        f"(source: {entry['source_conversation_id']}, at: {entry['at']})"
        for entry in entries
    ]
    return "\n".join(lines)


def _row_to_version_dict(row) -> dict:
    """``{"version", "entries", "created_by", "created_at"}`` -- the shape
    every read-a-version method returns, built identically from any row
    carrying those four columns (:data:`_GET_CURRENT_SQL`, :data:`_GET_
    VERSION_SQL`, and ``_INSERT_VERSION_SQL``'s own ``RETURNING`` all
    project exactly these four column names, in this order, on purpose)."""
    return {
        "version": row.version,
        "entries": row.entries,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
    }


_GET_CURRENT_SQL = text(
    "SELECT version, entries, created_by, created_at FROM user_memory "
    "WHERE user_sub = :user_sub ORDER BY version DESC LIMIT 1"
)

_LIST_VERSIONS_SQL = text(
    "SELECT version, created_by, created_at, jsonb_array_length(entries) AS entry_count "
    "FROM user_memory WHERE user_sub = :user_sub ORDER BY version DESC"
)

_MAX_VERSION_SQL = text(
    "SELECT COALESCE(MAX(version), 0) FROM user_memory WHERE user_sub = :user_sub"
)

_INSERT_VERSION_SQL = text(
    "INSERT INTO user_memory (user_sub, version, entries, created_by) "
    "VALUES (:user_sub, :version, :entries, :created_by) "
    "RETURNING version, entries, created_by, created_at"
)

_GET_VERSION_SQL = text(
    "SELECT version, entries, created_by, created_at FROM user_memory "
    "WHERE user_sub = :user_sub AND version = :version"
)

# Keeps the newest `keep_versions` rows for this user -- see write_version's
# own docstring for how `cutoff` is derived from the version that was just
# inserted.
_PRUNE_OLD_VERSIONS_SQL = text(
    "DELETE FROM user_memory WHERE user_sub = :user_sub AND version <= :cutoff"
)


class MemoryStore:
    """The per-process entry point: holds the ``Engine`` (and the resolved
    ``app_role``) and hands out per-user facades. Construction touches
    nothing -- no connection is opened until a :class:`UserMemory` method
    actually runs one (mirrors ``core/chat/history.py``'s own
    ``HistoryStore``)."""

    def __init__(self, engine: Engine, app_role: str | None = None) -> None:
        self._engine = engine
        self._app_role = app_role

    def for_user(self, user_sub: str) -> "UserMemory":
        """A facade scoped to ``user_sub`` -- every statement it runs goes
        through :func:`~poseidon.core.db.rls_transaction` with THIS
        identity, so nothing it does can ever touch another user's row."""
        return UserMemory(self._engine, user_sub, self._app_role)


class UserMemory:
    """One user's own versioned memory document. Every public method opens
    exactly one :func:`~poseidon.core.db.rls_transaction` except
    :meth:`restore` (an explicit lookup, then a delegated call to
    :meth:`write_version`, which opens its own -- see that method's own
    docstring) and :meth:`render_markdown` (delegates to :meth:`get_
    current`)."""

    def __init__(self, engine: Engine, user_sub: str, app_role: str | None = None) -> None:
        self._engine = engine
        self._user_sub = user_sub
        self._app_role = app_role

    def _transaction(self):
        return rls_transaction(self._engine, self._user_sub, app_role=self._app_role)

    def get_current(self) -> dict | None:
        """``{"version", "entries", "created_by", "created_at"}`` for this
        user's newest version, or ``None`` if :meth:`write_version` has
        never been called for this user -- there is no "default shape" for
        memory the way :class:`~poseidon.core.personalization.profile.
        UserProfile` has one for an instruction (an empty document is a
        real, meaningful "no memory yet" state, not the same as an empty
        entries list some version explicitly wrote)."""
        with self._transaction() as conn:
            row = conn.execute(_GET_CURRENT_SQL, {"user_sub": self._user_sub}).first()
        if row is None:
            return None
        return _row_to_version_dict(row)

    def list_versions(self) -> list[dict]:
        """``[{"version", "created_by", "created_at", "entry_count"}]``,
        newest first -- the settings surface's version-history list (doc 01
        section 9). ``entry_count`` is computed in SQL
        (``jsonb_array_length``) rather than in Python after the fact, so
        this method never has to materialize each version's full
        ``entries`` payload just to count it."""
        with self._transaction() as conn:
            rows = conn.execute(_LIST_VERSIONS_SQL, {"user_sub": self._user_sub}).all()
        return [
            {
                "version": row.version,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat(),
                "entry_count": row.entry_count,
            }
            for row in rows
        ]

    def write_version(
        self, entries: list[dict], created_by: Literal["user", "distiller"]
    ) -> dict:
        """Validate, size-check, insert version ``max(existing) + 1``,
        prune, return the new version's dict -- see the module docstring
        for why validation and the size cap both run, and both raise,
        strictly BEFORE any row is inserted (:class:`MemoryValidationError`
        / :class:`MemoryTooLarge` respectively), and for why retention
        pruning runs AFTER the insert, in the same transaction.

        ``cutoff = next_version - settings.memory_keep_versions``: rows
        with ``version <= cutoff`` are exactly the ones older than the
        newest ``memory_keep_versions`` as of THIS write (the version just
        inserted counts as the newest of the kept set) -- e.g. inserting
        version 23 with ``memory_keep_versions=20`` yields ``cutoff=3``,
        deleting versions 1-3 and keeping 4-23 (20 rows). A ``cutoff`` of
        zero or less (fewer versions exist than the retention window
        allows) deletes nothing -- the DELETE's own ``version <= :cutoff``
        predicate is never true for the always-positive ``version`` column
        in that case, so no separate guard is needed here."""
        _validate_entries(entries)
        rendered = _render_entries_markdown(entries)
        settings = get_settings()
        if len(rendered) > settings.memory_max_chars:
            raise MemoryTooLarge(
                f"rendered memory ({len(rendered)} chars) exceeds "
                f"settings.memory_max_chars={settings.memory_max_chars}"
            )
        with self._transaction() as conn:
            max_version = conn.execute(
                _MAX_VERSION_SQL, {"user_sub": self._user_sub}
            ).scalar_one()
            next_version = max_version + 1
            row = conn.execute(
                _INSERT_VERSION_SQL,
                {
                    "user_sub": self._user_sub,
                    "version": next_version,
                    "entries": json.dumps(entries),
                    "created_by": created_by,
                },
            ).first()
            cutoff = next_version - settings.memory_keep_versions
            conn.execute(
                _PRUNE_OLD_VERSIONS_SQL, {"user_sub": self._user_sub, "cutoff": cutoff}
            )
        return _row_to_version_dict(row)

    def restore(self, version: int) -> dict:
        """Append a NEW version carrying ``version``'s own entries
        verbatim, with ``created_by="user"`` even when the version being
        restored was ``created_by="distiller"`` -- restoring is always a
        user-initiated act, regardless of who authored the content being
        restored. Raises ``LookupError`` if ``version`` does not exist for
        this user. Never rewrites the old version's own row -- it stays
        exactly as it was, one more entry in the history, since this
        module's whole append-only design (doc 05 section 5: "a bad
        distillation is a one-click restore") depends on restoring never
        destroying the very history a future restore might need again.

        Two transactions, not one: the lookup below, then a full delegated
        call to :meth:`write_version` (its own transaction, its own
        validation, its own size check, its own retention prune) -- the
        entries being restored already passed validation once when they
        were first written, but re-validating costs nothing and keeps
        :meth:`write_version` the single place that contract is enforced,
        with no second, parallel insert path that could drift from it."""
        with self._transaction() as conn:
            row = conn.execute(
                _GET_VERSION_SQL, {"user_sub": self._user_sub, "version": version}
            ).first()
        if row is None:
            raise LookupError(
                f"user_memory version {version} does not exist for this user"
            )
        return self.write_version(row.entries, created_by="user")

    def render_markdown(self) -> str:
        """This user's current memory, rendered to markdown -- ``""`` if
        :meth:`get_current` returns ``None`` (no version yet). Uses the
        SAME private renderer (:func:`_render_entries_markdown`)
        :meth:`write_version`'s own size-cap check measures against, so
        the two can never disagree about what "the rendered form" of a
        given entries list is."""
        current = self.get_current()
        if current is None:
            return ""
        return _render_entries_markdown(current["entries"])


__all__ = [
    "MemoryStore",
    "MemoryTooLarge",
    "MemoryValidationError",
    "UserMemory",
]

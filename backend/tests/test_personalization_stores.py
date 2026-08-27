"""Tests for Phase 13 Task 1 (doc 05 section 5): migration 0008's three
personalization tables (``user_profile``, ``user_memory``, ``memory_
outbox``) and their stores (``poseidon.core.personalization.profile.
ProfileStore``, ``poseidon.core.personalization.memory.MemoryStore``,
``poseidon.core.personalization.outbox.OutboxStore``).

Mirrors ``test_feedback_store.py``'s pg conventions exactly: a module-level
probe skips the whole file if ``DATABASE_URL`` is unset, unreachable, or the
migration has not been applied yet; every fixture row this suite needs is
built through a real store (``HistoryStore`` for conversations, never a bare
INSERT for CONTENT rows), then the store under test is driven directly.

One section per store's own concerns (the task brief's own organization),
plus a catalog section proving migration 0008's own DDL shape directly --
in particular the two deliberate RLS divergences from every precedent since
migration 0005 (no ``poseidon_admin`` policy/grant on any of the three
tables; a DELETE grant on ``user_memory`` alone) -- so a regression to
either shows up here, not only in a reviewer's manual re-read of the
migration diff.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text

from poseidon.core import personalization as personalization_module
from poseidon.core.chat.history import HistoryStore
from poseidon.core.config import Settings, get_settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import rls_transaction
from poseidon.core.personalization.memory import MemoryStore, MemoryTooLarge, MemoryValidationError
from poseidon.core.personalization.outbox import OutboxStore
from poseidon.core.personalization.profile import (
    INSTRUCTION_MAX_CHARS,
    InstructionTooLarge,
    ProfileStore,
)
from poseidon.core.util.uuid7 import uuid7

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0008)"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg personalization-store tests need a Postgres: "
        f"{_UP_HINT}, {_MIGRATE_HINT}",
        allow_module_level=True,
    )

# Mirrors test_feedback_store.py's own module-level probe exactly, narrowed
# to "does user_profile exist" (0008, not merely 0007) -- see that module's
# docstring for why this is a probe, not an assumption. All three tables in
# this migration are created together, so checking one is enough to know
# whether 0008 has run.
_DSN_ROLE_IS_SUPERUSER = False
try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'user_profile'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"user_profile does not exist - {_MIGRATE_HINT}",
                    allow_module_level=True,
                )
            _cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            _DSN_ROLE_IS_SUPERUSER = _cur.fetchone()[0]
except psycopg.Error as exc:
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )

_APP_ROLE = "poseidon_app"
_ADMIN_ROLE = "poseidon_admin"
_EFFECTIVE_APP_ROLE = _APP_ROLE if _DSN_ROLE_IS_SUPERUSER else None

_USER_PROFILE = "user_profile"
_USER_MEMORY = "user_memory"
_MEMORY_OUTBOX = "memory_outbox"
_PERSONALIZATION_TABLES = (_USER_PROFILE, _USER_MEMORY, _MEMORY_OUTBOX)


# ---------------------------------------------------------------------------
# fixtures and small helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_engine():
    from poseidon.core.db import build_engine

    engine = build_engine(_DSN)
    try:
        yield engine
    finally:
        engine.dispose()


def _fresh_user_sub() -> str:
    return f"test|{uuid.uuid4().hex}"


def _entry(entry_type: str, statement: str, *, source_conversation_id=None, at=None) -> dict:
    return {
        "type": entry_type,
        "statement": statement,
        "source_conversation_id": source_conversation_id or str(uuid7()),
        "at": at or "2026-08-04T12:00:00+00:00",
    }


def _create_conversation(pg_engine, user_sub: str) -> str:
    history = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    conversation, _opener = history.create_conversation()
    return conversation["id"]


@pytest.fixture
def settings_override(monkeypatch):
    """Applies an override for one or more ``Settings`` fields for the
    duration of a single test, hermetically -- mirrors ``test_harvest_
    cost.py``'s own ``_clear_settings_env``/``get_settings.cache_clear()``
    discipline: every Settings-derived env var is cleared first (so no
    ambient shell value leaks in), ``DATABASE_URL``/``S3_BUCKET`` are
    re-set to what this suite actually needs, then the caller's own
    overrides are applied. ``get_settings`` is ``lru_cache``-wrapped (one
    process-wide singleton, by design, for the real long-lived server) --
    cleared both before AND after so this fixture's own monkeypatched env
    never leaks a stale ``Settings`` into whichever test runs next in this
    same pytest process."""
    applied = {"done": False}

    def _apply(**overrides) -> None:
        for key in (name.upper() for name in Settings.model_fields):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("DATABASE_URL", _DSN)
        monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
        for key, value in overrides.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()
        applied["done"] = True

    yield _apply
    if applied["done"]:
        get_settings.cache_clear()


# ===========================================================================
# offline: ASCII-on-disk (house rule; matches test_feedback_store.py's own
# precedent)
# ===========================================================================


def test_personalization_modules_and_this_test_module_are_ascii_on_disk():
    from poseidon.core.personalization import memory as memory_module
    from poseidon.core.personalization import outbox as outbox_module
    from poseidon.core.personalization import profile as profile_module

    paths = [
        Path(personalization_module.__file__),
        Path(profile_module.__file__),
        Path(memory_module.__file__),
        Path(outbox_module.__file__),
        Path(__file__),
    ]
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ===========================================================================
# catalog assertions -- migration 0008 built what the rest of this file
# relies on, and proves both deliberate RLS divergences directly against
# the live database (mirrors test_feedback_store.py's own catalog section)
# ===========================================================================


@pytest.mark.parametrize("table", _PERSONALIZATION_TABLES)
def test_row_level_security_is_enabled_and_forced(pg_engine, table):
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"
            ),
            {"t": table},
        ).first()

    assert row is not None, f"{table} must exist (migration 0008)"
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


@pytest.mark.parametrize(
    "table,policy",
    [
        (_USER_PROFILE, "user_profile_owner"),
        (_USER_MEMORY, "user_memory_owner"),
        (_MEMORY_OUTBOX, "memory_outbox_owner"),
    ],
)
def test_owner_policy_exists_with_the_pinned_predicate(pg_engine, table, policy):
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cmd, qual, with_check FROM pg_policies "
                "WHERE tablename = :t AND policyname = :p"
            ),
            {"t": table, "p": policy},
        ).first()

    assert row is not None, f"{policy} policy must exist"
    assert row.cmd == "ALL"
    assert "current_setting" in row.qual and "app.user_sub" in row.qual and "user_sub" in row.qual
    assert (
        "current_setting" in row.with_check
        and "app.user_sub" in row.with_check
        and "user_sub" in row.with_check
    )


#: Every policy each personalization table is allowed to carry.
#:
#: Was "exactly ``<table>_owner``, nothing else" until Phase 14 Task 3,
#: which adds migration 0009's ``memory_outbox_worker`` -- the memory
#: worker's claim role, ``USING (true)`` on that ONE table. Widened here
#: rather than in the assertion below so the allowance is a visible,
#: table-by-table list a reviewer reads at a glance instead of a special
#: case buried in a test body.
_EXPECTED_POLICIES = {
    _USER_PROFILE: {"user_profile_owner"},
    _USER_MEMORY: {"user_memory_owner"},
    _MEMORY_OUTBOX: {"memory_outbox_owner", "memory_outbox_worker"},
}


@pytest.mark.parametrize("table", _PERSONALIZATION_TABLES)
def test_no_admin_policy_exists_on_any_of_the_three_tables(pg_engine, table):
    """Divergence 1 (migration 0008's own docstring, doc 05 section 7):
    "Admins have no path to another user's messages, user_memory, or
    user_profile."

    Phase 14 Task 3 note: this used to assert "exactly one policy, the
    owner policy" -- a proxy for "nobody added an admin policy" that was
    strictly stronger than the rule it stood for, and that migration 0009's
    ``memory_outbox_worker`` (a MACHINE role for the worker's cross-user
    claim, on ``memory_outbox`` only -- never a human read path to anyone's
    memory or profile) therefore trips. The allow-list above replaces the
    proxy, and the second assertion below now pins the actual rule
    directly: no policy on any of these tables may be granted to
    ``poseidon_admin``, whatever else exists.
    """
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT policyname, roles FROM pg_policies WHERE tablename = :t"), {"t": table}
        ).all()

    names = {row.policyname for row in rows}
    assert names == _EXPECTED_POLICIES[table], (
        f"{table} must carry exactly the policies this codebase sanctions for it, "
        f"got: {sorted(names)}"
    )
    for row in rows:
        assert _ADMIN_ROLE not in (row.roles or []), (
            f"{row.policyname} names {_ADMIN_ROLE}: doc 05 section 7 gives admins no path "
            f"to another user's {table}"
        )


@pytest.mark.parametrize("table", _PERSONALIZATION_TABLES)
def test_poseidon_admin_has_no_privileges_at_all(pg_engine, table):
    """Divergence 1, continued: no GRANT to poseidon_admin either -- a
    policy alone grants no privilege, so both halves of "no admin path"
    are checked independently."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = :t"
            ),
            {"role": _ADMIN_ROLE, "t": table},
        ).all()

    assert rows == [], f"poseidon_admin must have zero privileges on {table}, got: {rows}"


def test_poseidon_app_grants_on_user_profile_select_insert_update_no_delete(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = 'user_profile'"
            ),
            {"role": _APP_ROLE},
        ).all()

    assert {row.privilege_type for row in rows} == {"SELECT", "INSERT", "UPDATE"}


def test_poseidon_app_grants_on_user_memory_select_insert_delete_no_update(pg_engine):
    """Divergence 2 (migration 0008's own docstring): the first DELETE
    grant on any RLS table since migration 0005 -- version-retention
    pruning is ordinary log-rotation, not an audit record. No UPDATE:
    versions are immutable once written."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = 'user_memory'"
            ),
            {"role": _APP_ROLE},
        ).all()

    assert {row.privilege_type for row in rows} == {"SELECT", "INSERT", "DELETE"}


def test_poseidon_app_grants_on_memory_outbox_select_insert_update_no_delete(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = 'memory_outbox'"
            ),
            {"role": _APP_ROLE},
        ).all()

    assert {row.privilege_type for row in rows} == {"SELECT", "INSERT", "UPDATE"}


# ===========================================================================
# ProfileStore / UserProfile
# ===========================================================================


def test_profile_get_on_never_written_user_returns_default_shape_not_an_error(pg_engine):
    store = ProfileStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(_fresh_user_sub())

    assert store.get() == {"system_instruction": "", "updated_at": None}


def test_profile_put_then_get_round_trips(pg_engine):
    user_sub = _fresh_user_sub()
    store = ProfileStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    put_result = store.put("always show GP in USD thousands")

    assert put_result["system_instruction"] == "always show GP in USD thousands"
    assert put_result["updated_at"] is not None
    assert store.get() == put_result


def test_profile_put_twice_amends_the_same_row(pg_engine):
    user_sub = _fresh_user_sub()
    store = ProfileStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    store.put("first instruction")

    store.put("second instruction")

    with pg_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM user_profile WHERE user_sub = :u"), {"u": user_sub}
        ).scalar_one()
    assert count == 1, "the user_sub primary key enforces exactly one row per user"
    assert store.get()["system_instruction"] == "second instruction"


def test_two_users_profiles_never_leak_into_each_others_get(pg_engine):
    sub_a = _fresh_user_sub()
    sub_b = _fresh_user_sub()
    store = ProfileStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE)
    store.for_user(sub_a).put("a's instruction")
    store.for_user(sub_b).put("b's instruction")

    assert store.for_user(sub_a).get()["system_instruction"] == "a's instruction"
    assert store.for_user(sub_b).get()["system_instruction"] == "b's instruction"


# Final whole-phase review, finding I-2: the memory document is capped at
# exactly one point (``UserMemory.write_version``, MemoryTooLarge -> 422),
# but the instruction -- the half a user TYPES, injected into every router
# and synthesis prompt on every subsequent turn -- had no cap anywhere, in
# the route body model or in this store. A single oversized PUT therefore
# broke that user's every later turn, permanently, with no way back except
# another PUT. Capped here rather than in ``api/me.py``'s request model for
# the same reason ``memory.py``'s own module docstring gives for its cap
# ("enforcement lives at exactly ONE point"): the store is the single path
# every writer -- HTTP route today, anything else later -- has to go
# through, so the cap cannot be bypassed by adding a second caller.
def test_profile_put_over_the_instruction_cap_raises_and_writes_nothing(pg_engine):
    user_sub = _fresh_user_sub()
    store = ProfileStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    with pytest.raises(InstructionTooLarge):
        store.put("x" * (INSTRUCTION_MAX_CHARS + 1))

    with pg_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM user_profile WHERE user_sub = :u"), {"u": user_sub}
        ).scalar_one()
    assert count == 0, "an oversized instruction must upsert nothing"


def test_profile_put_at_exactly_the_instruction_cap_is_accepted(pg_engine):
    """The boundary is inclusive, matching ``write_version``'s own
    ``len(rendered) > settings.memory_max_chars`` comparison -- a document
    exactly AT the cap is within it."""
    user_sub = _fresh_user_sub()
    store = ProfileStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    at_cap = "x" * INSTRUCTION_MAX_CHARS

    written = store.put(at_cap)

    assert written["system_instruction"] == at_cap
    assert store.get()["system_instruction"] == at_cap


def test_profile_put_does_not_clobber_an_existing_instruction_when_rejected(pg_engine):
    """The raise happens before any SQL runs, so a rejected oversized PUT
    leaves the user's PREVIOUS instruction exactly as it was -- the same
    "raises BEFORE any row is inserted" property ``write_version``'s own
    size check has."""
    user_sub = _fresh_user_sub()
    store = ProfileStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    store.put("the instruction that must survive")

    with pytest.raises(InstructionTooLarge):
        store.put("y" * (INSTRUCTION_MAX_CHARS + 1))

    assert store.get()["system_instruction"] == "the instruction that must survive"


# ===========================================================================
# MemoryStore / UserMemory
# ===========================================================================


def test_memory_get_current_on_never_written_user_returns_none(pg_engine):
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(_fresh_user_sub())

    assert store.get_current() is None


def test_memory_write_version_then_get_current_round_trips_at_version_1(pg_engine):
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    entries = [_entry("preference", "prefers USD thousands")]

    written = store.write_version(entries, created_by="user")

    assert written["version"] == 1
    assert written["created_by"] == "user"
    assert written["entries"] == entries
    current = store.get_current()
    assert current == written


def test_memory_second_write_version_lands_at_2_and_get_current_returns_newest(pg_engine):
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    store.write_version([_entry("fact", "first version")], created_by="user")

    second = store.write_version([_entry("fact", "second version")], created_by="distiller")

    assert second["version"] == 2
    current = store.get_current()
    assert current["version"] == 2
    assert current["created_by"] == "distiller"


def test_memory_list_versions_returns_both_newest_first_with_entry_count(pg_engine):
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    store.write_version([_entry("fact", "one")], created_by="user")
    store.write_version(
        [_entry("fact", "two"), _entry("scope", "three")], created_by="distiller"
    )

    versions = store.list_versions()

    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["created_by"] == "distiller"
    assert versions[0]["entry_count"] == 2
    assert versions[1]["created_by"] == "user"
    assert versions[1]["entry_count"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: {**e, "type": "not-a-real-type"},
        lambda e: {**e, "statement": ""},
        lambda e: {**e, "statement": "   "},
        lambda e: {**e, "source_conversation_id": None},
        lambda e: {**e, "source_conversation_id": ""},
        lambda e: {**e, "at": None},
        lambda e: {**e, "at": ""},
        lambda e: {k: v for k, v in e.items() if k != "type"},
        lambda e: {k: v for k, v in e.items() if k != "statement"},
        lambda e: {k: v for k, v in e.items() if k != "source_conversation_id"},
        lambda e: {k: v for k, v in e.items() if k != "at"},
    ],
)
def test_memory_write_version_with_a_malformed_entry_raises_before_any_insert(
    pg_engine, mutate
):
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    bad_entry = mutate(_entry("preference", "a valid statement"))

    with pytest.raises(MemoryValidationError):
        store.write_version([bad_entry], created_by="user")

    with pg_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM user_memory WHERE user_sub = :u"), {"u": user_sub}
        ).scalar_one()
    assert count == 0, "a malformed entry must insert nothing"


def test_memory_write_version_over_a_tiny_max_chars_raises_memory_too_large_and_inserts_nothing(
    pg_engine, settings_override
):
    settings_override(memory_max_chars=10)
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    entries = [_entry("preference", "this statement is far longer than ten characters")]

    with pytest.raises(MemoryTooLarge):
        store.write_version(entries, created_by="user")

    with pg_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM user_memory WHERE user_sub = :u"), {"u": user_sub}
        ).scalar_one()
    assert count == 0, "an oversized write must insert nothing"


def test_memory_write_version_allocates_versions_atomically_under_concurrent_writers(
    pg_engine,
):
    """Round-0 review fix regression test: ``next_version = MAX(version) +
    1`` computed in Python, followed by a separate INSERT, is a read-then-
    write race -- two concurrent write_version calls for the SAME user_sub
    could both read the same MAX and both attempt to insert the same
    (user_sub, version) primary key, the second raising an unhandled
    IntegrityError instead of landing a distinct, correctly-allocated
    version. write_version now opens each transaction with
    ``pg_advisory_xact_lock(hashtext(user_sub))`` BEFORE reading
    MAX(version), serializing concurrent writers for the same user.

    Drives several threads at the SAME user's memory concurrently (each on
    its own DB connection, checked out from the shared engine's pool --
    genuine concurrent transactions, not merely interleaved Python
    bytecode) and asserts every one succeeds, with a DISTINCT, gap-free
    version number and no exception -- proving the allocation is atomic
    rather than read-then-write, not merely hoping a lucky interleaving
    reveals the bug."""
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    writer_count = 8
    barrier = threading.Barrier(writer_count)
    results: list[int] = []
    errors: list[Exception] = []
    results_lock = threading.Lock()

    def _write(i: int) -> None:
        barrier.wait()  # line every thread up to call write_version near-simultaneously
        try:
            written = store.write_version(
                [_entry("fact", f"concurrent statement {i}")], created_by="user"
            )
            with results_lock:
                results.append(written["version"])
        except Exception as exc:  # captured for the assertion below, not swallowed
            with results_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(writer_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"no concurrent writer should raise -- got: {errors}"
    assert sorted(results) == list(range(1, writer_count + 1)), (
        f"every writer must land a DISTINCT, gap-free version -- got: {sorted(results)}"
    )
    with pg_engine.connect() as conn:
        db_versions = conn.execute(
            text("SELECT version FROM user_memory WHERE user_sub = :u ORDER BY version"),
            {"u": user_sub},
        ).all()
    assert [v.version for v in db_versions] == list(range(1, writer_count + 1))


def test_memory_restore_creates_a_new_version_forcing_created_by_user(pg_engine):
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    v1_entries = [_entry("fact", "version one statement")]
    store.write_version(v1_entries, created_by="user")
    store.write_version([_entry("correction", "version two statement")], created_by="distiller")
    with pg_engine.connect() as conn:
        before = conn.execute(
            text(
                "SELECT user_sub, version, entries, created_by, created_at FROM user_memory "
                "WHERE user_sub = :u AND version = 1"
            ),
            {"u": user_sub},
        ).first()

    restored = store.restore(1)

    assert restored["version"] == 3, "restore must APPEND, not rewrite"
    assert restored["created_by"] == "user", (
        "restore always attributes to the user, even restoring a distiller version"
    )
    assert restored["entries"] == v1_entries
    with pg_engine.connect() as conn:
        versions = conn.execute(
            text("SELECT version FROM user_memory WHERE user_sub = :u ORDER BY version"),
            {"u": user_sub},
        ).all()
        after = conn.execute(
            text(
                "SELECT user_sub, version, entries, created_by, created_at FROM user_memory "
                "WHERE user_sub = :u AND version = 1"
            ),
            {"u": user_sub},
        ).first()
    assert [v.version for v in versions] == [1, 2, 3]
    assert after == before, "the restored-FROM version's own row must be byte-unchanged"


def test_memory_restore_of_a_nonexistent_version_raises_lookuperror(pg_engine):
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(_fresh_user_sub())

    with pytest.raises(LookupError):
        store.restore(99)


def test_memory_write_version_prunes_to_keep_versions(pg_engine, settings_override):
    settings_override(memory_keep_versions=3)
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    for i in range(6):  # memory_keep_versions(3) + 3
        store.write_version([_entry("fact", f"statement {i}")], created_by="user")

    with pg_engine.connect() as conn:
        versions = conn.execute(
            text("SELECT version FROM user_memory WHERE user_sub = :u ORDER BY version"),
            {"u": user_sub},
        ).all()
    assert [v.version for v in versions] == [4, 5, 6], (
        "only the newest memory_keep_versions rows must survive pruning"
    )


# Final whole-phase review, finding I-3: with memory_keep_versions at 0,
# `cutoff = next_version - 0` equals the version that was just inserted, so
# the prune DELETE removed the row the INSERT three lines above it had
# written -- in the SAME transaction. write_version still returned that
# version's dict, so a caller (and `PUT /api/me/memory`) reported a 200
# carrying a version number that was not in the table, and this user's
# memory silently never persisted at all. The misconfiguration is a
# plausible one precisely because this codebase reads 0 as "off" everywhere
# else it means anything (`token_spike_threshold`,
# `rate_limit_chat_per_minute`), so a value below 1 now means "keep
# everything" rather than "delete everything."
def test_memory_write_version_with_keep_versions_zero_prunes_nothing(
    pg_engine, settings_override
):
    settings_override(memory_keep_versions=0)
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    first = store.write_version([_entry("fact", "statement one")], created_by="user")
    second = store.write_version([_entry("fact", "statement two")], created_by="user")

    assert (first["version"], second["version"]) == (1, 2), (
        "each write must allocate the NEXT version -- a deleted-on-write row "
        "makes MAX(version) reset to 0 and every write reuse version 1"
    )
    current = store.get_current()
    assert current is not None, "the version write_version just returned must exist in the table"
    assert current["version"] == 2
    with pg_engine.connect() as conn:
        versions = conn.execute(
            text("SELECT version FROM user_memory WHERE user_sub = :u ORDER BY version"),
            {"u": user_sub},
        ).all()
    assert [v.version for v in versions] == [1, 2], (
        "memory_keep_versions below 1 must prune nothing, never delete the just-written row"
    )


def test_two_users_memory_never_leaks(pg_engine):
    sub_a = _fresh_user_sub()
    sub_b = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE)
    a_entries = [_entry("fact", "a's own fact")]
    b_entries = [_entry("fact", "b's own fact")]
    store.for_user(sub_a).write_version(a_entries, created_by="user")
    store.for_user(sub_b).write_version(b_entries, created_by="user")

    assert store.for_user(sub_a).get_current()["entries"] == a_entries
    assert store.for_user(sub_b).get_current()["entries"] == b_entries
    assert len(store.for_user(sub_a).list_versions()) == 1
    assert len(store.for_user(sub_b).list_versions()) == 1


def test_render_markdown_is_empty_for_a_never_written_user(pg_engine):
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(_fresh_user_sub())

    assert store.render_markdown() == ""


def test_render_markdown_uses_the_same_renderer_write_versions_size_check_uses(
    pg_engine, settings_override
):
    user_sub = _fresh_user_sub()
    store = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    entries = [_entry("preference", "a short statement")]
    store.write_version(entries, created_by="user")

    rendered = store.render_markdown()

    assert rendered != ""
    # If render_markdown and write_version's own size-cap check used two
    # DIFFERENT renderers that could disagree, this override (one character
    # short of what render_markdown just measured) would not reliably
    # reject the identical entries below.
    settings_override(memory_max_chars=len(rendered) - 1)
    with pytest.raises(MemoryTooLarge):
        store.write_version(entries, created_by="user")


# ===========================================================================
# OutboxStore / ConversationOutbox
# ===========================================================================


def test_outbox_touch_on_a_new_conversation_id_inserts_one_pending_row(pg_engine):
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    store = OutboxStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    store.touch(cid)

    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, attempts, last_turn_at FROM memory_outbox "
                "WHERE conversation_id = :c"
            ),
            {"c": cid},
        ).first()
    assert row is not None
    assert row.status == "pending"
    assert row.attempts == 0


def test_outbox_touch_again_updates_the_same_row_and_bumps_last_turn_at(pg_engine):
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    store = OutboxStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    store.touch(cid)
    with pg_engine.connect() as conn:
        first = conn.execute(
            text("SELECT last_turn_at FROM memory_outbox WHERE conversation_id = :c"), {"c": cid}
        ).first()

    time.sleep(0.01)
    store.touch(cid)

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT last_turn_at FROM memory_outbox WHERE conversation_id = :c"), {"c": cid}
        ).all()
    assert len(rows) == 1, "touch() must amend the SAME row, never insert a second one"
    assert rows[0].last_turn_at > first.last_turn_at


def test_outbox_touch_on_a_previously_done_row_resets_to_pending_and_clears_attempts(
    pg_engine,
):
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    with rls_transaction(pg_engine, user_sub, app_role=_EFFECTIVE_APP_ROLE) as conn:
        conn.execute(
            text(
                "INSERT INTO memory_outbox "
                "(conversation_id, user_sub, last_turn_at, status, attempts, last_error) "
                "VALUES (:c, :u, now(), 'done', 3, :err)"
            ),
            {"c": cid, "u": user_sub, "err": json.dumps({"reason": "prior failure"})},
        )
    store = OutboxStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    store.touch(cid)

    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, attempts, last_error FROM memory_outbox "
                "WHERE conversation_id = :c"
            ),
            {"c": cid},
        ).first()
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.last_error is None


def test_two_users_outbox_rows_never_leak_and_no_admin_escape_hatch_is_relied_on(pg_engine):
    """This test also doubles as this task's own proof that the owner-only
    RLS policy (no admin exception) actually holds -- there is no admin
    role escape hatch on memory_outbox to accidentally rely on here, only
    each user's own rls_transaction identity."""
    sub_a = _fresh_user_sub()
    sub_b = _fresh_user_sub()
    cid_a = _create_conversation(pg_engine, sub_a)
    cid_b = _create_conversation(pg_engine, sub_b)
    store = OutboxStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE)
    store.for_user(sub_a).touch(cid_a)
    store.for_user(sub_b).touch(cid_b)

    with rls_transaction(pg_engine, sub_a, app_role=_EFFECTIVE_APP_ROLE) as conn:
        rows_a = conn.execute(text("SELECT conversation_id FROM memory_outbox")).all()
    with rls_transaction(pg_engine, sub_b, app_role=_EFFECTIVE_APP_ROLE) as conn:
        rows_b = conn.execute(text("SELECT conversation_id FROM memory_outbox")).all()

    assert {str(r.conversation_id) for r in rows_a} == {cid_a}
    assert {str(r.conversation_id) for r in rows_b} == {cid_b}

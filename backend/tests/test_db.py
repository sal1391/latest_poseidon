"""Offline tests for :mod:`poseidon.core.db` (round-0 correction, doc 05
section 4, decision D28's "runtime enforcement on privileged DSNs"
amendment): the one behavior of ``rls_transaction``'s new ``app_role``
parameter that needs NO real Postgres to prove -- a malformed configured
role name must raise before any SQL is ever built or any connection is ever
opened, because ``SET ROLE`` accepts no bind parameter and the role name
has to be interpolated into SQL text by hand.

Everything else about ``app_role`` (that it actually issues ``SET LOCAL
ROLE`` against a real session, that ``None`` skips it while ``set_config``
still runs, that the wrapper's role-switch is what makes the four required
RLS tests pass on this compose database's privileged DSN) needs a real
Postgres and lives in ``test_rls_policies.py`` instead -- this module is
deliberately narrow: it is the one file in this pair that runs even when
``DATABASE_URL`` is unset, so a bad ``database_app_role`` value is caught
by the plain offline suite, not only by the pg suite.
"""

from pathlib import Path

import pytest

from poseidon.core import db as db_module
from poseidon.core.db import rls_transaction


class _BogusEngine:
    """A engine-shaped double whose ``begin()`` always fails loudly.

    Standing in for "no real connection must ever be attempted" -- if
    ``app_role`` validation happened anywhere near or after
    ``engine.begin()`` instead of strictly before it, this test would fail
    with THIS class's own ``AssertionError`` instead of the ``ValueError``
    it actually expects, making a future regression (validation moved past
    the connection-open point) fail loudly and specifically rather than
    just silently start hitting a real database.
    """

    def begin(self):  # pragma: no cover - only reached by a real regression
        raise AssertionError("engine.begin() must never be called for an invalid app_role")


@pytest.mark.parametrize(
    "bad_role",
    [
        "1starts-with-digit",
        "Has-Upper-Case",
        "has space",
        "has;semicolon",
        "has\"quote",
        "",
        "a" * 64,  # 64 chars: one over the {0,62} cap (63 total)
    ],
)
def test_invalid_app_role_raises_before_any_connection_is_opened(bad_role):
    with pytest.raises(ValueError, match="app_role"):
        with rls_transaction(_BogusEngine(), "test|does-not-matter", app_role=bad_role):
            pass  # pragma: no cover - must never be reached


def test_valid_app_role_names_pass_validation_unharmed():
    """The pattern's own edge cases: a bare single letter, a name at
    exactly the 63-char cap, and underscores throughout -- none of these
    should be rejected. Uses the same ``_BogusEngine`` to prove the
    validation step itself accepts them (raising ``AssertionError`` from
    ``begin()``, never ``ValueError``, is the expected outcome here)."""
    for good_role in ("a", "poseidon_app", "_leading_underscore", "a" * 63):
        with pytest.raises(AssertionError, match="engine.begin"):
            with rls_transaction(_BogusEngine(), "test|does-not-matter", app_role=good_role):
                pass  # pragma: no cover - must never be reached


def test_db_module_is_ascii_on_disk():
    """Matches the codebase-wide ASCII-on-disk convention (e.g.
    ``test_runlog_module_is_ascii_on_disk``)."""
    for path in (Path(db_module.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"

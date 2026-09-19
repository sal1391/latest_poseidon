"""Task 7 (Phase 15, PLACEHOLDER): the Entra-vs-Snowflake-stored-procedure
email source switch -- ``Settings.identity_email_source``/``Settings.
snowflake_email_proc`` and ``core/email_source.py``'s ``resolve_email``.

Status: PLACEHOLDER. Carlos is waiting on the stored-procedure code from the
Snowflake side; this task ships only the switch and the seam -- a setting, a
resolver interface, and a stub that refuses loudly. It does NOT call
Snowflake, and it does NOT wire the resolver into ``identity_spcs.py`` (that
wiring is a follow-up task once the real procedure code exists).

Four cases, matching the task brief exactly:
1. ``identity_email_source`` defaults to ``"entra"``.
2. The ``entra`` resolver returns ``None`` -- today's unchanged behaviour
   (``Sf-Context-Current-User`` carries a bare username; no email claim
   exists to read -- see ``identity_spcs.py``'s own module docstring).
3. Selecting ``snowflake_proc`` without ``SNOWFLAKE_EMAIL_PROC`` fails at
   ``Settings`` construction -- a misconfigured deploy dies at boot, not at
   first login.
4. Selecting ``snowflake_proc`` WITH a procedure name configured constructs
   cleanly, but ``resolve_email`` itself raises ``NotImplementedError`` with
   the exact placeholder message -- the stub that refuses.
"""

import pytest
from pydantic import ValidationError

REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}


def make_settings(monkeypatch, **overrides):
    from poseidon.core.config import Settings

    # Hermetic: clear EVERY Settings env var (derived, so the list can't drift)
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    env = {**REQUIRED, **overrides}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_identity_email_source_defaults_to_entra(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.identity_email_source == "entra"


def test_entra_resolver_returns_none(monkeypatch):
    from poseidon.core.email_source import resolve_email

    s = make_settings(monkeypatch)
    assert resolve_email("alice", s) is None


def test_snowflake_proc_without_proc_name_fails_at_settings_construction(monkeypatch):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, IDENTITY_EMAIL_SOURCE="snowflake_proc")


def test_snowflake_proc_resolver_raises_not_implemented(monkeypatch):
    from poseidon.core.email_source import resolve_email

    s = make_settings(
        monkeypatch,
        IDENTITY_EMAIL_SOURCE="snowflake_proc",
        SNOWFLAKE_EMAIL_PROC="DB.SCHEMA.GET_USER_EMAIL",
    )
    with pytest.raises(
        NotImplementedError,
        match=r"^snowflake_proc email source is a placeholder; awaiting stored procedure code$",
    ):
        resolve_email("alice", s)

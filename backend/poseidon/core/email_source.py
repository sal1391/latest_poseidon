"""Phase 15 Task 7 (PLACEHOLDER): the Entra-vs-Snowflake-stored-procedure
email source switch.

**Status: PLACEHOLDER.** Carlos is waiting on the stored-procedure code from
the Snowflake side. This module ships only the switch and the seam: a
resolver function with two branches, one of which (``entra``) is today's
real, unchanged behaviour, and one of which (``snowflake_proc``) is a stub
that raises loudly rather than calling Snowflake. When the procedure code
arrives, a follow-up task fills the ``snowflake_proc`` branch in; nothing
else about this seam should need to move.

**Why it exists.** In ``spcs_ingress`` mode ``Sf-Context-Current-User``
carries a bare username and ``identity_spcs.py`` leaves ``email``/``name``
as ``None`` (see that module's own docstring) -- the email is assumed today
to come from Entra (Microsoft Entra ID, the SSO in front of Snowflake), but
this app never actually resolves it. ``Settings.identity_email_source``
names a second, configurable source: a Snowflake stored procedure that maps
the username to an email.

**Settings, not a global.** ``resolve_email`` takes ``settings`` as an
explicit argument rather than reading ``get_settings()`` itself -- the same
"providers never import FastAPI/api, everything explicit" discipline
``identity.py``'s own module docstring pins for the identity providers --
so a test can pass any ``Settings`` it likes with zero ambient state.

**Do NOT wire this into ``identity_spcs.py`` yet.** The stub raises, so
wiring it into the request path now would break the default path's tests
for nothing. Wiring lands in the follow-up task once real procedure code
exists.
"""

from poseidon.core.config import Settings

_SNOWFLAKE_PROC_PLACEHOLDER_MESSAGE = (
    "snowflake_proc email source is a placeholder; awaiting stored procedure code"
)


def resolve_email(username: str, settings: Settings) -> str | None:
    """Resolve ``username`` to an email address per ``settings.
    identity_email_source``.

    ``entra`` returns ``None`` -- exactly what the identity providers return
    today (no email claim exists to read in spcs_ingress mode; see
    ``identity_spcs.py``'s own module docstring). ``snowflake_proc`` is the
    stub: it always raises ``NotImplementedError``, never calls Snowflake.
    ``Settings.snowflake_email_proc_required_when_selected`` already
    guarantees ``settings.snowflake_email_proc`` is set whenever this branch
    is reachable, so this function does not re-check it.
    """
    if settings.identity_email_source == "entra":
        return None
    raise NotImplementedError(_SNOWFLAKE_PROC_PLACEHOLDER_MESSAGE)


__all__ = ["resolve_email"]

"""The internal contract Next.js dispatches skills through.

This router is NOT public. It exists because the application layer moved to
Next.js (decision M1) while every skill stayed in Python (M2), so the caller
is now another service rather than a browser.

**Identity is passed explicitly and applied here.** app.py's identity
middleware sets ``request.state.user`` on every request, but on THIS route
that user is the web tier's own connection, not the person asking. So the
caller states the subject and roles in the body and this module builds the
``SkillContext`` from those instead -- deliberately ignoring
``request.state.user``. Getting this backwards would scope every user's
query to whatever identity the service connects as.

**The body is a claim, not a credential.** Nothing here verifies the stated
sub: this module trusts it completely, exactly the way ``SpcsIngressProvider``
trusts an ingress header. The trust therefore has to be established *around*
this route rather than inside it -- the web tier resolved the real identity
before calling (``web/src/proxy.ts``), and whatever network boundary keeps
this route unreachable from outside is what makes that the only possible
caller. Mount it where the internet can reach it and any caller can pose as
any user. That is the same property the SPCS ingress seam already has, named
here so it is a decision rather than an oversight.

**Not "every response is HTTP 200"**, which is ``api/dev_runner.py``'s
contract (see its module docstring) and deliberately not this one. A router
loop reads a structured failure and reacts; the caller here is an HTTP client
in another runtime (``web/src/lib/skills.ts``) that throws on a non-2xx, so
the two faults that mean *no skill ran at all* are HTTP statuses:

* a body without a well-formed ``identity`` -> 422, from pydantic;
* an unregistered ``skill_id`` -> 404.

Once a skill does run, its :class:`~poseidon.core.skills.result.SkillResult`
comes back verbatim inside a 200 -- ``ok=False`` included. A skill that failed
answered the question, and the web tier renders that answer.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

# The wire shape is ``dev_runner``'s, imported rather than copied so the
# ``ArtifactRef`` flattening exists once. It is a private name across a module
# boundary, which is a smell -- kept anyway, and narrowly: it is one dict
# literal (promoting it would edit a third module for no behavior change), and
# ``app.py`` already imports ``dev_runner`` unconditionally in every habitat,
# so this import adds no coupling that boot did not already have. The seam to
# revisit is whenever the dev-only runner is retired.
from poseidon.api.dev_runner import _serialize
from poseidon.core.data.synthetic_client import SyntheticDataClient
from poseidon.core.identity import UserContext
from poseidon.core.skills.context import ConversationSlots, SkillContext

router = APIRouter(prefix="/internal/v1", tags=["internal"])


class _Identity(BaseModel):
    """The caller's statement of who is asking.

    ``sub`` is required with no default, on purpose: a missing identity is a
    422 rather than an anonymous dispatch, because every persisted row and
    every RLS policy keys off the sub and an empty one silently matches
    nothing (or writes a row nobody can read back).

    A *blank* sub is the same fault wearing a value. ``min_length=1`` catches
    ``""``; the validator below catches every string that is only whitespace,
    which pydantic would otherwise hand straight through to
    ``set_config('app.user_sub', '   ', true)``.
    """

    sub: str = Field(min_length=1)
    roles: list[str] = Field(default_factory=list)

    @field_validator("sub")
    @classmethod
    def _reject_a_blank_sub(cls, value: str) -> str:
        """Reject a whitespace-only sub. ``ValueError`` on purpose: pydantic
        wraps it into the ``ValidationError`` FastAPI renders as a 422, so this
        fault reads exactly like the missing-key one it really is.

        The value comes back UNCHANGED rather than stripped. A sub is the key
        RLS matches on, and every provider mints it with no surrounding
        whitespace (``core/identity.py``'s ``sanitize_username``), so silently
        rewriting one here would make this route the only place in the system
        where the sub that was sent is not the sub that was used -- a
        normalisation that fixes a caller's bug invisibly instead of failing
        closed on it.
        """
        if not value.strip():
            raise ValueError("sub must not be blank")
        return value


class _DispatchRequest(BaseModel):
    args: dict
    identity: _Identity


@router.post("/skills/{skill_id}/dispatch")
def dispatch_skill(skill_id: str, body: _DispatchRequest, request: Request) -> dict[str, Any]:
    """Dispatch ``skill_id`` with ``body.args`` under ``body.identity`` and
    return the serialized :class:`~poseidon.core.skills.result.SkillResult`.

    ``registry.get`` raises ``KeyError`` for an unregistered id (it does not
    return ``None``), which is the 404 below. ``registry.dispatch`` then
    *never* raises -- a rejected argument or a skill bug comes back as a
    ``SkillResult`` with ``ok=False`` -- so there is no error path after it to
    handle here, only a serialization.
    """
    registry = request.app.state.skill_registry
    try:
        registry.get(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    settings = request.app.state.settings
    ctx = SkillContext(
        data=SyntheticDataClient(settings.database_url),
        # Defensively, mirroring app.py's own pattern at app.py:177: the store
        # is assigned in two places (``_wire_live_chat``, and the
        # ``deploy_mode == "local"`` block) and an spcs+mock app runs neither,
        # so the attribute need not exist. ``None`` is the established
        # semantic for "this dispatch cannot produce a file" (app.py:170's own
        # comment); an AttributeError is not.
        artifacts=getattr(request.app.state, "artifact_store", None),
        settings=settings,
        state=ConversationSlots(),
        user=UserContext(
            sub=body.identity.sub,
            email=None,
            name=None,
            # A tuple, because UserContext is frozen and a list field would
            # leave the identity mutable through it.
            roles=tuple(body.identity.roles),
        ),
    )
    return _serialize(registry.dispatch(skill_id, body.args, ctx))

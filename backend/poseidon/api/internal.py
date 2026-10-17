"""The internal contract Next.js dispatches skills and chat turns through.

This router is NOT public. It exists because the application layer moved to
Next.js (decision M1) while every skill stayed in Python (M2), so the caller
is now another service rather than a browser.

Two routes: ``POST /skills/{skill_id}/dispatch`` (Phase 15) runs one skill;
``POST /conversations/{cid}/turns`` (Phase 16 Task 9) runs one whole chat
turn and streams its SSE frames. The second is the PUBLIC chat-send route
with its identity taken from the body -- not a re-implementation of it but
the very same function, ``live_chat.stream_turn``, which the public route
now also merely adapts onto. Everything below that seam (the thread+queue
bridge, the snowflake guard, titles at turn one, persist-before-terminal-
frame) therefore exists once.

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
here so it is a decision rather than an oversight. Since Phase 16 the caller
must also present ``POSEIDON_INTERNAL_TOKEN`` (``internal_auth.py``); the
network boundary is defence in depth, no longer the only gate.

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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

# The two shared halves of the public chat-send route's own guards -- see
# ``api/auth.py``'s module docstring for why they exist as plain-value
# functions: a FastAPI dependency reads the request, and this router's
# identity is never in the request.
from poseidon.api.auth import assert_sales, check_chat_rate_limit

# The wire shape is ``dev_runner``'s, imported rather than copied so the
# ``ArtifactRef`` flattening exists once. It is a private name across a module
# boundary, which is a smell -- kept anyway, and narrowly: it is one dict
# literal (promoting it would edit a third module for no behavior change), and
# ``app.py`` already imports ``dev_runner`` unconditionally in every habitat,
# so this import adds no coupling that boot did not already have. The seam to
# revisit is whenever the dev-only runner is retired.
from poseidon.api.dev_runner import _serialize
from poseidon.api.internal_auth import require_internal_token

# One direction only: this module imports ``live_chat``, ``live_chat`` never
# imports this one. ``app.py`` imports both (its single ``from poseidon.api
# import ...`` line), so the cycle this would create if the arrow were ever
# reversed would break boot itself, not merely a test.
from poseidon.api.live_chat import stream_turn
from poseidon.core.data.factory import DataBackendUnavailable, build_data_client
from poseidon.core.identity import UserContext
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.result import problem

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Phase 16 Task 1: never published. The OpenAPI document is a public
    # surface; an internal contract listed there invites callers it must
    # refuse. test_internal_dispatch.py proves the routes are still mounted
    # by calling them.
    include_in_schema=False,
    dependencies=[Depends(require_internal_token)],
)


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
def dispatch_skill(skill_id: str, body: _DispatchRequest, request: Request) -> Any:
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
    try:
        data = build_data_client(settings)
    except DataBackendUnavailable as exc:
        return JSONResponse(
            status_code=501, content=problem(501, "backend not implemented", str(exc))
        )
    ctx = SkillContext(
        data=data,
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
        # Phase 17 PR A2: getattr for the same reason as artifact_store above;
        # a direct skill call belongs to no conversation.
        report_store=getattr(request.app.state, "report_store", None),
        prompt_tree=getattr(request.app.state, "prompt_tree", None),
        conversation_id=None,
    )
    return _serialize(registry.dispatch(skill_id, body.args, ctx))


class _TurnRequest(BaseModel):
    """The public chat-send body (``live_chat.SendBody``: ``text`` plus an
    optional ``client_turn_key``) with ``identity`` added -- the one field
    that differs between the two routes, for the reason this module's own
    docstring gives."""

    identity: _Identity
    text: str
    client_turn_key: str | None = None


@router.post("/conversations/{cid}/turns")
async def stream_conversation_turn(
    cid: str, body: _TurnRequest, request: Request
) -> StreamingResponse:
    """``POST /api/conversations/{cid}/messages`` with identity from the
    body (spec section 5.1): the SAME turn, streaming the SAME SSE frames,
    because it is literally the same function --
    :func:`~poseidon.api.live_chat.stream_turn`, which the public route is
    now also just an adapter onto. ``tests/test_internal_turns.py`` proves
    it by streaming one scripted turn through both routes and comparing the
    frames; nothing else here is allowed to grow, or that proof stops
    meaning anything.

    Requires ``CHAT_MODE=live``: the history store and the rest of the turn
    wiring exist only then (``app.py``'s own ``_wire_live_chat``), so a
    mock-mode app refuses with a 503 rather than an ``AttributeError``
    halfway into a stream whose status line already said 200.

    The two guards the public route attaches as dependencies
    (``require_sales``, then ``rate_limit_chat_send``) are applied HERE,
    inline, in that same order -- a FastAPI dependency reads the request,
    and this route's identity is not in the request. They are the shared
    halves of those same two functions (``auth.assert_sales`` /
    ``auth.check_chat_rate_limit``), never a second copy of the rule.
    """
    app_state = request.app.state
    if getattr(app_state, "history_store", None) is None:
        raise HTTPException(status_code=503, detail="CHAT_MODE=live is required for internal turns")
    user = assert_sales(
        UserContext(
            sub=body.identity.sub,
            email=None,
            name=None,
            # A tuple, for the same reason dispatch_skill's own identity is
            # one: UserContext is frozen and a list field would leave it
            # mutable through the frozen wrapper.
            roles=tuple(body.identity.roles),
        )
    )
    # Keyed by the BODY's sub, not request.state.user's -- the latter is the
    # web tier's own connection identity, shared by every person using the
    # app, so keying on it would put everyone in one bucket.
    check_chat_rate_limit(request, user.sub)
    return await stream_turn(
        cid=cid,
        text=body.text,
        client_turn_key=body.client_turn_key,
        user=user,
        app_state=app_state,
    )

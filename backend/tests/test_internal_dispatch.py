"""Tests for the internal Next.js-to-Python skill-dispatch contract
(``poseidon.api.internal``) and its wiring into ``create_app``.

Unlike ``api/dev_runner.py`` -- the other HTTP surface that dispatches a skill
-- this router is mounted in EVERY habitat, because after the Next.js
migration it is the only way the application tier reaches a skill at all. The
two habitat tests below pin that (``local`` and ``spcs``), and with it the
unconditional ``SkillRegistry.discover()`` in ``app.py`` that makes it
possible.

Also unlike ``dev_runner``, this route is NOT "every response is HTTP 200":
a body that does not carry an identity is a 422 and an unregistered
``skill_id`` is a 404, both at the HTTP level, because the caller is another
service whose own client raises on a non-2xx (``web/src/lib/skills.ts``)
rather than a router loop that reads a structured failure. Once a real skill
runs, its ``SkillResult`` -- ``ok=False`` included -- comes back verbatim
inside a 200, exactly as ``dev_runner`` serializes it.

Every test here builds a placeholder-DSN app that never touches a real
database. The envelope test dispatches ``data_qa.metric_query`` with
well-formed arguments, so the skill DOES run and DOES fail to reach Postgres;
that failure is the point -- what is asserted is the envelope's five keys
surviving the trip, not the numbers inside it. The pg-backed golden for that
skill already lives in ``tests/test_dev_runner.py`` and in the skill's own
test module; re-driving it here would buy a second copy of the same
assertion, not new coverage.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from poseidon.core.config import Settings
from poseidon.core.identity import DISABLED_DEFAULT_USER
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.registry import RegisteredSkill, SkillRegistry
from poseidon.core.skills.result import SkillResult, text_part

DISPATCH_PATH_TEMPLATE = "/internal/v1/skills/{skill_id}/dispatch"

# Present-and-non-blank is all Settings needs from a DSN it never successfully
# connects with -- mirrors tests/test_dev_runner.py's own placeholder.
_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

_SALES_IDENTITY = {"sub": "dev|local", "roles": ["Poseidon:Sales"]}


def _settings(**overrides) -> Settings:
    """Explicit Settings rather than ``create_app()``'s own ``get_settings()``
    -- ``database_url`` has no default, so a bare ``create_app()`` would make
    these tests depend on an ambient ``DATABASE_URL`` (or a host ``.env``)
    for both their pass and their meaning. Same helper shape as
    tests/test_dev_runner.py."""
    defaults: dict = dict(
        _env_file=None, database_url=_PLACEHOLDER_DSN, s3_bucket="poseidon-artifacts"
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _app(**overrides):
    from poseidon.api.app import create_app

    return create_app(_settings(**overrides))


@pytest.fixture()
def client():
    return TestClient(_app())


# ---------------------------------------------------------------------------
# habitat: mounted everywhere, registry always built
# ---------------------------------------------------------------------------


def test_the_route_is_mounted_in_every_habitat():
    """The internal contract is how the web tier dispatches EVERY skill, so
    unlike ``dev_runner``'s local-only surface it must exist in ``spcs`` and
    ``ec2`` too -- and the registry it reads off ``app.state`` must exist
    with it. ``spcs`` + the default ``chat_mode="mock"`` is the combination
    that used to build no registry at all (the deleted conditional at
    app.py's discovery call), which would have made this route an
    ``AttributeError`` on a real deploy."""
    for deploy_mode in ("local", "spcs", "ec2"):
        app = _app(deploy_mode=deploy_mode)
        assert DISPATCH_PATH_TEMPLATE in app.openapi()["paths"], deploy_mode
        assert hasattr(app.state, "skill_registry"), deploy_mode
        assert "data_qa.metric_query" in app.state.skill_registry.skill_ids, deploy_mode


# ---------------------------------------------------------------------------
# input validation and unknown skills are HTTP-level failures
# ---------------------------------------------------------------------------


def test_dispatch_requires_identity(client):
    """No identity in the body is a malformed request, not an anonymous one:
    there is no fallback to the middleware's own user (see the module
    docstring of ``api/internal.py``), so a caller that omits it would
    otherwise get someone else's scope."""
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
        json={"args": {}},
    )
    assert resp.status_code == 422


def test_dispatch_requires_a_sub_inside_the_identity(client):
    """``identity: {}`` is the same fault one level down -- an empty object
    must not resolve to an empty sub, which is a value RLS would happily
    match nothing (or, worse, a row written under it) against."""
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
        json={"args": {}, "identity": {"roles": ["Poseidon:Sales"]}},
    )
    assert resp.status_code == 422


def test_dispatch_rejects_an_empty_sub(client):
    """``{"sub": ""}`` is the missing-key fault wearing a value: pydantic is
    perfectly happy with an empty string, and what it would reach is
    ``set_config('app.user_sub', '', true)`` -- an identity that matches no row
    and writes rows nobody can read back."""
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
        json={"args": {}, "identity": {"sub": "", "roles": ["Poseidon:Sales"]}},
    )
    assert resp.status_code == 422


def test_dispatch_rejects_a_whitespace_only_sub(client):
    """And the same fault with a length, which ``min_length`` alone does not
    catch."""
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
        json={"args": {}, "identity": {"sub": "   ", "roles": ["Poseidon:Sales"]}},
    )
    assert resp.status_code == 422


def test_dispatch_rejects_an_unknown_skill(client):
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="does_not.exist"),
        json={"args": {}, "identity": _SALES_IDENTITY},
    )
    assert resp.status_code == 404
    assert "does_not.exist" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# the SkillResult envelope, verbatim
# ---------------------------------------------------------------------------


def test_dispatch_returns_a_skill_result_envelope(client):
    """All five keys of ``dev_runner._serialize``'s wire shape, from a real
    dispatch of a real skill. The DSN is unreachable, so ``ok`` is False and
    ``error`` carries the structured failure -- which is exactly the case
    worth pinning: a failing skill is still a 200 carrying the envelope, not
    an HTTP error."""
    resp = client.post(
        DISPATCH_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
        json={
            "args": {
                "entity": "MARINE_SALES_PLANNING_V",
                "metrics": ["GP"],
                "period": {"start": "2026-04-01", "end": "2026-05-01"},
            },
            "identity": _SALES_IDENTITY,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"ok", "parts", "proof", "artifacts", "error"}
    assert isinstance(body["ok"], bool)
    assert isinstance(body["parts"], list)


def test_a_skills_result_is_passed_through_untouched():
    """The envelope is a serialization, not a transformation: whatever the
    skill returned is what the caller reads. Asserted against a hand-built
    registry so the expected value is written here rather than derived from
    whatever a real skill happens to produce today."""
    parts = [text_part("hello from the probe")]
    app = _app()
    app.state.skill_registry = _registry_returning(
        SkillResult(ok=True, parts=parts, proof=["ontology:probe"])
    )
    resp = TestClient(app).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id=_PROBE_SKILL_ID),
        json={"args": {}, "identity": _SALES_IDENTITY},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "parts": parts,
        "proof": ["ontology:probe"],
        "artifacts": [],
        "error": None,
    }


# ---------------------------------------------------------------------------
# identity: the body's, never the middleware's
# ---------------------------------------------------------------------------


def test_the_body_identity_reaches_the_skill_not_the_middleware_user():
    """**The load-bearing property of this whole module.** ``app.py``'s
    identity middleware resolves ``request.state.user`` for every request,
    this one included -- but on this route that user is the web tier's own
    connection, not the person asking. The ``X-Dev-User: alice`` header below
    makes the two provably different (``dev|alice`` vs the body's
    ``sf|CAROL``): the skill must see the body's."""
    seen: list[object] = []
    app = _app()
    app.state.skill_registry = _registry_recording(seen)
    resp = TestClient(app).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id=_PROBE_SKILL_ID),
        json={
            "args": {},
            "identity": {"sub": "sf|CAROL", "roles": ["Poseidon:Analyst"]},
        },
        headers={"X-Dev-User": "alice"},
    )
    assert resp.status_code == 200
    (user,) = seen
    assert user.sub == "sf|CAROL"
    assert user.roles == ("Poseidon:Analyst",)
    # Not the middleware's -- neither its act-as sub nor its default one.
    assert user.sub not in ("dev|alice", DISABLED_DEFAULT_USER.sub)
    assert user.roles != DISABLED_DEFAULT_USER.roles


def test_roles_default_to_empty_and_are_a_tuple():
    """``roles`` is optional on the wire (a caller with no roles sends none)
    but ``UserContext.roles`` is a ``tuple[str, ...]``, so the conversion has
    to happen here -- a list would make the frozen identity mutable through
    its own field."""
    seen: list[object] = []
    app = _app()
    app.state.skill_registry = _registry_recording(seen)
    resp = TestClient(app).post(
        DISPATCH_PATH_TEMPLATE.format(skill_id=_PROBE_SKILL_ID),
        json={"args": {}, "identity": {"sub": "dev|nobody"}},
    )
    assert resp.status_code == 200
    (user,) = seen
    assert user.roles == ()
    assert user.email is None and user.name is None


# ---------------------------------------------------------------------------
# probe-skill scaffolding -- a hand-built SkillRegistry, no filesystem walk
# (mirrors test_emit_seam_loop_events.py's own no-discovery construction)
# ---------------------------------------------------------------------------

_PROBE_SKILL_ID = "probe.identity"


class _NoArgs(BaseModel):
    """Every probe skill here takes no arguments."""


def _registry(fn) -> SkillRegistry:
    return SkillRegistry(
        skills={
            _PROBE_SKILL_ID: RegisteredSkill(
                skill_id=_PROBE_SKILL_ID,
                args_model=_NoArgs,
                fn=fn,
                description="A probe skill for the internal dispatch tests -- never a real one.",
            )
        }
    )


def _registry_returning(result: SkillResult) -> SkillRegistry:
    def _run(_ctx: SkillContext, _args: _NoArgs) -> SkillResult:
        return result

    return _registry(_run)


def _registry_recording(seen: list[object]) -> SkillRegistry:
    def _run(ctx: SkillContext, _args: _NoArgs) -> SkillResult:
        seen.append(ctx.user)
        return SkillResult(ok=True)

    return _registry(_run)

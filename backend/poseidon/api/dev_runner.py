"""Dev-only skill runner — the only way to call a skill before the real chat
pipeline exists (Phase 6) and, forever after that, the fastest way to
reproduce a skill's exact output outside a full conversation turn.

``create_app`` includes this router — and builds ``app.state.skill_registry``
via :meth:`~poseidon.core.skills.registry.SkillRegistry.discover` — only when
``settings.deploy_mode == "local"``. Neither exists in ``spcs``/``ec2``: this
is a development surface, never a production one, and the conditional include
in ``app.py`` is what keeps it out of any deployed habitat, rather than a
runtime permission check that could be bypassed or misconfigured.

**Every response is HTTP 200.** ``POST /api/dev/skills/{skill_id}/run`` hands
its JSON body, unmodified, to
:meth:`~poseidon.core.skills.registry.SkillRegistry.dispatch` and serializes
whatever :class:`~poseidon.core.skills.result.SkillResult` comes back.
Dispatch never raises (see its own docstring): an unknown ``skill_id`` is a
404, bad arguments are a 422, and a skill bug is a 500 — but all three are
*structured content* inside the response body's ``error`` field
(``ok: false``), not an HTTP-level error status. This is deliberate, not an
oversight: it mirrors the contract the real router loop (Phase 5) will run
on, where a "failure" is something the loop reads and reacts to, never an
exception it must survive. So, explicitly: an unknown ``skill_id`` posted
here does NOT come back as a 404 response from this endpoint — it comes back
as a 200 whose body carries ``ok: false`` and ``error.status == 404``,
produced by the registry's own dispatch, exactly like every other structured
failure.

The data-backend guard below (:func:`_build_ctx`) never reaches dispatch at
all — there is no client to hand it when ``data_backend`` names an adapter
that does not exist yet — but it is *also* returned as a 200 with a
structured 501, for exactly the same "failure is content" reason.

The only ways this endpoint answers with a non-200 status are HTTP-level
concerns FastAPI itself owns before this module's code ever runs: a
malformed JSON body, or the whole router being absent because ``deploy_mode``
isn't ``"local"`` (a genuine 404, since the route was never mounted).
"""

from typing import Any

from fastapi import APIRouter, Request

from poseidon.core.data.synthetic_client import SyntheticDataClient
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.result import SkillResult, problem

router = APIRouter(prefix="/api/dev", tags=["dev"])


def _build_ctx(request: Request) -> SkillContext | SkillResult:
    """Assemble a fresh :class:`SkillContext` for one request, or a
    structured 501 :class:`SkillResult` when the configured data backend has
    no adapter yet.

    ``SyntheticDataClient`` is cheap to build (a DSN string; it opens no
    network connection until a query actually runs), so it is built fresh per
    request and needs no pooling or caching at this phase. The
    :class:`~poseidon.core.artifacts.ArtifactStore` is NOT built here: it is
    the process-wide one ``create_app`` built and called ``ensure_bucket()``
    on at start-up, so every request shares one boto3 client and the bucket
    check happens once per process rather than never.
    """
    settings = request.app.state.settings
    if settings.data_backend != "synthetic":
        return SkillResult(
            ok=False,
            error=problem(
                501,
                "backend not implemented",
                f"data_backend={settings.data_backend!r} has no adapter yet - "
                "the Snowflake client arrives Phase 15; the dev runner only "
                "supports data_backend=synthetic today",
            ),
        )
    return SkillContext(
        data=SyntheticDataClient(settings.database_url),
        artifacts=request.app.state.artifact_store,
        settings=settings,
        state=ConversationSlots(),
    )


def _serialize(result: SkillResult) -> dict[str, Any]:
    """The wire shape: ``SkillResult`` verbatim, with ``ArtifactRef`` frozen
    dataclasses (not JSON-serializable on their own) flattened to the plain
    ``{"name", "url", "mime"}`` dicts the frontend contract expects."""
    return {
        "ok": result.ok,
        "parts": result.parts,
        "proof": result.proof,
        "artifacts": [
            {"name": ref.name, "url": ref.url, "mime": ref.mime} for ref in result.artifacts
        ],
        "error": result.error,
    }


@router.post("/skills/{skill_id}/run")
def run_skill(skill_id: str, args: dict[str, Any], request: Request) -> dict[str, Any]:
    """Dispatch ``skill_id`` with the raw ``args`` body and return the
    serialized :class:`~poseidon.core.skills.result.SkillResult`.

    See the module docstring for the "always 200, failure is structured
    content" contract — including for an unknown ``skill_id``, which is not a
    404 *response* from this endpoint but a 200 whose body carries
    ``error.status == 404``.
    """
    ctx = _build_ctx(request)
    if isinstance(ctx, SkillResult):
        return _serialize(ctx)
    registry = request.app.state.skill_registry
    result = registry.dispatch(skill_id, args, ctx)
    return _serialize(result)

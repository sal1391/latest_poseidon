from fastapi import FastAPI
from sqlalchemy import create_engine

from poseidon.api import dev_runner, health, live_chat, mock_chat
from poseidon.core.artifacts import ArtifactStore
from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings, get_settings
from poseidon.core.data.synthetic_client import SyntheticDataClient
from poseidon.core.llm.bedrock import BedrockProvider
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.runlog import RunLogWriter
from poseidon.core.skills.registry import SkillRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with: python -m uvicorn poseidon.api.app:create_app --factory"""
    app = FastAPI(title="Poseidon API", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    if app.state.settings.chat_mode == "live":
        _wire_live_chat(app)
    else:
        # Default ("mock"), and every existing env until an operator opts in
        # via CHAT_MODE=live -- byte-identical to every Phase 1-5 behavior
        # (mock_chat.py's own module docstring). The two routers are never
        # mounted together.
        app.include_router(mock_chat.router)
    if app.state.settings.deploy_mode == "local":
        # The dev skill runner is a local-only surface (poseidon.api.dev_runner's
        # module docstring): built once per app/process rather than per request,
        # since discovery walks and imports the whole poseidon.tasks tree
        # (SkillRegistry.discover's own fail-fast contract). Non-local habitats
        # build neither the registry nor the router here -- Phase 6's own answer
        # for spcs/ec2 needing a registry is CHAT_MODE=live above, which builds
        # its OWN registry independent of deploy_mode (a live chat pipeline
        # needs one in every habitat, not just local dev).
        app.state.skill_registry = SkillRegistry.discover()
        # One store per app/process, shared by every request (dev_runner's
        # _build_ctx reads it off app.state), and one bucket check at boot so
        # the first skill that writes a PDF does not meet a NoSuchBucket.
        # Constructing the store is pure local work — boto3.client opens no
        # connection — so only ensure_bucket() can fail here, and it is
        # deliberately non-fatal: a developer with MinIO stopped must still be
        # able to boot the API and run every skill that produces no artifact
        # (which is all of them today). The warning is the honest record that
        # artifact writes will fail until MinIO is back.
        app.state.artifact_store = ArtifactStore(app.state.settings)
        try:
            app.state.artifact_store.ensure_bucket()
        except Exception as exc:  # noqa: BLE001 - any store failure must not block local boot
            print(
                f"WARNING: artifact bucket '{app.state.settings.s3_bucket}' is not ready "
                f"({type(exc).__name__}: {exc}); artifact uploads will fail until "
                "MinIO/S3 is reachable",
                flush=True,
            )
        # flush=True: uvicorn --reload runs this inside a long-lived worker
        # subprocess whose stdout is piped back through the reloader rather
        # than a TTY, so an unflushed print can sit in Python's block buffer
        # and never reach `docker compose logs` at all (unlike a short-lived
        # `python -m ...` startup step, which flushes for free at exit).
        print(f"skills registered: {', '.join(app.state.skill_registry.skill_ids)}", flush=True)
        app.include_router(dev_runner.router)
    return app


def _wire_live_chat(app: FastAPI) -> None:
    """Construction wiring for ``settings.chat_mode == "live"`` (Phase 6 Task
    4): everything ``live_chat.py``'s route handlers read off ``app.state``
    per request, then mount ``live_chat.router``.

    Built once per app/process, the same "cheap to construct, safe to share"
    discipline ``deploy_mode == "local"``'s own ``skill_registry``/
    ``artifact_store`` wiring above already uses: ``SkillRegistry.discover()``
    is deterministic and import-only (registry.py's own module docstring),
    ``RoleClient``/``PromptRegistry`` touch only a packaged YAML file and a
    prompts directory, ``ConversationStateStore`` is explicitly meant to be
    ONE shared instance (its whole job is being shared mutable state across
    requests -- see its own module docstring), and ``SyntheticDataClient``
    holds nothing but a DSN string (its own module docstring), so one
    long-lived instance behaves identically to a fresh one per request --
    unlike ``dev_runner.py``'s per-request construction, there is no
    connection or other per-call state here to keep isolated between
    requests. ``RoleClient`` registers BOTH the stub router
    (``DevDeterministicRouter``, what actually answers in ``LLM_MODE=stub``)
    and the real Bedrock provider (what answers in ``LLM_MODE=live`` with
    ``llm_profile=bedrock``) -- the two canonical shapes ``roles.py``'s own
    docstring documents; registering both at once is harmless
    (``RoleClient.invoke`` reads exactly one key per call).
    """
    settings = app.state.settings
    app.state.skill_registry = SkillRegistry.discover()
    app.state.conversation_state_store = ConversationStateStore()
    app.state.role_client = RoleClient(
        settings, providers={"stub": DevDeterministicRouter(), "bedrock": BedrockProvider()}
    )
    prompts_dir = settings.prompts_dir if settings.prompts_dir is not None else DEFAULT_PROMPTS_DIR
    app.state.prompt_registry = PromptRegistry(prompts_dir)
    app.state.data_client = SyntheticDataClient(settings.database_url)
    app.state.run_log_writer = _build_run_log_writer(settings)
    app.include_router(live_chat.router)


def _build_run_log_writer(settings: Settings) -> RunLogWriter | None:
    """``RunLogWriter`` over an engine built from ``DATABASE_URL``, or
    ``None`` when that fails -- disclosed either way with a boot log line,
    the same non-fatal, honestly-logged shape ``ensure_bucket()`` above
    already uses for a MinIO/S3 that is not reachable yet.

    ``Settings.database_url`` only enforces "not blank" (``not_blank``'s own
    validator); it is not itself proof that the value is a URL SQLAlchemy's
    ``create_engine`` can parse. ``create_engine`` never opens a network
    connection (engines are lazy -- see ``health.py``'s own ``/ready`` probe
    and ``SyntheticDataClient``'s module docstring for the identical
    convention), so the only realistic way this ever fails is a
    syntactically malformed DSN; a merely-unreachable host still builds a
    real writer here; that writer only ever fails later, per call, inside
    its OWN never-raises methods (``runlog.py``'s module docstring).
    """
    try:
        engine = create_engine(settings.database_url)
        writer = RunLogWriter(engine)
    except Exception as exc:  # noqa: BLE001 - a bad DATABASE_URL must not block live chat booting
        print(
            f"WARNING: RunLogWriter could not be built from DATABASE_URL "
            f"({type(exc).__name__}: {exc}); chat turns will not be logged to the run log",
            flush=True,
        )
        return None
    print("run-log writer: enabled (DATABASE_URL configured)", flush=True)
    return writer

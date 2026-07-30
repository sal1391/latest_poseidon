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
from poseidon.mcp.registry import ToolServerRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with: python -m uvicorn poseidon.api.app:create_app --factory"""
    app = FastAPI(title="Poseidon API", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.include_router(health.router)

    # Discovery walks and imports the whole poseidon.tasks tree (SkillRegistry.
    # discover's own fail-fast contract) -- built ONCE per app/process here,
    # ahead of both blocks below, and shared by whichever of them actually
    # need it: live chat wiring, the local dev runner, or both at once when an
    # operator runs CHAT_MODE=live locally (fix round 1, MINOR M1 -- these two
    # conditions used to each build their own registry independently, quietly
    # discarding one of the two identical walks whenever both fired). A
    # mock-mode, non-local app needs neither and builds nothing, unchanged.
    if app.state.settings.chat_mode == "live" or app.state.settings.deploy_mode == "local":
        app.state.skill_registry = SkillRegistry.discover()

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
        # module docstring): app.state.skill_registry is already built above,
        # since deploy_mode == "local" is one of the two conditions that
        # triggers it.
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

    ``app.state.skill_registry`` is NOT built here -- ``create_app`` already
    built it above, before calling this function, since ``chat_mode ==
    "live"`` is one of the two conditions that triggers that discovery walk
    (fix round 1, MINOR M1: this function used to call ``SkillRegistry.
    discover()`` a second time whenever ``deploy_mode == "local"`` ALSO held,
    silently discarding one of the two identical walks).

    Everything else here is built once per app/process, the same "cheap to
    construct, safe to share" discipline ``deploy_mode == "local"``'s own
    ``artifact_store`` wiring uses: ``RoleClient``/``PromptRegistry`` touch
    only a packaged YAML file and a prompts directory, ``ConversationState
    Store``/``TranscriptStore`` (Task 5 amendment) are both explicitly meant
    to be ONE shared instance each (their whole job is being shared mutable
    state across requests -- see their own module docstrings), and
    ``SyntheticDataClient`` holds nothing but a DSN string (its own module
    docstring), so one long-lived instance behaves identically to a fresh
    one per request -- unlike ``dev_runner.py``'s per-request construction,
    there is no connection or other per-call state here to keep isolated
    between requests. ``RoleClient`` registers BOTH the stub router
    (``DevDeterministicRouter``, what actually answers in ``LLM_MODE=stub``)
    and the real Bedrock provider (what answers in ``LLM_MODE=live`` with
    ``llm_profile=bedrock``) -- the two canonical shapes ``roles.py``'s own
    docstring documents; registering both at once is harmless
    (``RoleClient.invoke`` reads exactly one key per call). ``tool_registry``
    (Phase 7 Task 4) follows the identical "build once, share" discipline --
    see :func:`_build_tool_registry`.
    """
    settings = app.state.settings
    app.state.conversation_state_store = ConversationStateStore()
    # Phase 6 Task 5 amendment: the live bootstrap routes' own transcript
    # store -- see live_chat.py's module docstring ("Task 5 amendment: the
    # live bootstrap routes") for why this is a SEPARATE object from
    # conversation_state_store above, not a field added to it.
    app.state.transcript_store = live_chat.TranscriptStore()
    app.state.role_client = RoleClient(
        settings, providers={"stub": DevDeterministicRouter(), "bedrock": BedrockProvider()}
    )
    prompts_dir = settings.prompts_dir if settings.prompts_dir is not None else DEFAULT_PROMPTS_DIR
    app.state.prompt_registry = PromptRegistry(prompts_dir)
    app.state.data_client = SyntheticDataClient(settings.database_url)
    app.state.run_log_writer = _build_run_log_writer(settings)
    app.state.tool_registry = _build_tool_registry(settings)
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


def _build_tool_registry(settings: Settings) -> ToolServerRegistry:
    """The ``ToolServerRegistry`` every live chat turn's ``SkillContext.
    tools`` resolves against (Phase 7 Task 4) -- see ``poseidon.mcp.
    registry.ToolServerRegistry``'s own docstring for the ``overrides``
    seam this function is the production caller of.

    AMENDED post-Task-2: a REAL ``PERPLEXITY_API_KEY`` can exist in an
    operator's ambient environment while the chat is still running
    ``LLM_MODE=stub`` -- key PRESENCE is the wrong gate for whether a
    research dispatch should hit the real Perplexity API, since it would
    silently burn API credits on every research-shaped demo/dev turn. The
    gate is ``settings.llm_mode`` instead, the SAME switch that already
    decides which LLM provider answers this app's ``role_client``
    (``_wire_live_chat``, above) -- doc 06's "stub LLM mode throughout":
    LLM and research move together, one mode switch governing every
    external call this app makes. ``"stub"`` installs a
    ``FixtureResearchTool`` override (bypassing transport resolution
    entirely, reading the clean, recorded fixture -- see that class's own
    module docstring); ``"live"`` supplies no override at all, leaving
    ``ToolServerRegistry`` to resolve a real transport per
    ``TOOL_TRANSPORT_PERPLEXITY`` exactly as it was designed to on its own.

    Never resolves eagerly, either mode: constructing the registry (and,
    under ``"stub"``, constructing ``FixtureResearchTool`` -- itself proven
    to read no file at construction time, only inside its own ``search()``)
    is a plain object build with no I/O of its own; only a skill's
    ``ctx.tools.research`` access ever triggers real resolution
    (``ToolServerRegistry``'s own laziness rule, unaffected by this
    function). The boot log line below reports the CONFIGURED choice, not
    a resolved instance, for exactly that reason -- reading ``.research``
    here just to log it would defeat the laziness this function otherwise
    preserves.
    """
    if settings.llm_mode == "stub":
        from poseidon.mcp.perplexity.fixture_tool import FixtureResearchTool

        overrides = {"research": FixtureResearchTool()}
        label = "fixture (llm_mode=stub)"
    else:
        overrides = None
        label = f"{settings.tool_transport_perplexity} (llm_mode=live)"
    print(f"research transport: {label}", flush=True)
    return ToolServerRegistry(settings, overrides=overrides)

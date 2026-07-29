from fastapi import FastAPI

from poseidon.api import dev_runner, health, mock_chat
from poseidon.core.config import Settings, get_settings
from poseidon.core.skills.registry import SkillRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with: python -m uvicorn poseidon.api.app:create_app --factory"""
    app = FastAPI(title="Poseidon API", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    app.include_router(mock_chat.router)
    if app.state.settings.deploy_mode == "local":
        # The dev skill runner is a local-only surface (poseidon.api.dev_runner's
        # module docstring): built once per app/process rather than per request,
        # since discovery walks and imports the whole poseidon.tasks tree
        # (SkillRegistry.discover's own fail-fast contract). Non-local habitats
        # build neither the registry nor the router — the real chat pipeline
        # that will need a registry in spcs/ec2 arrives in Phase 6.
        app.state.skill_registry = SkillRegistry.discover()
        # flush=True: uvicorn --reload runs this inside a long-lived worker
        # subprocess whose stdout is piped back through the reloader rather
        # than a TTY, so an unflushed print can sit in Python's block buffer
        # and never reach `docker compose logs` at all (unlike a short-lived
        # `python -m ...` startup step, which flushes for free at exit).
        print(
            f"skills registered: {', '.join(app.state.skill_registry.skill_ids)}", flush=True
        )
        app.include_router(dev_runner.router)
    return app

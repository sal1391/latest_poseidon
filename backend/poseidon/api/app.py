from fastapi import FastAPI

from poseidon.api import health, mock_chat
from poseidon.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with: python -m uvicorn poseidon.api.app:create_app --factory"""
    app = FastAPI(title="Poseidon API", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    app.include_router(mock_chat.router)
    return app

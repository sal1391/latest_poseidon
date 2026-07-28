from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    settings = request.app.state.settings
    try:
        engine = create_engine(
            settings.database_url, connect_args={"connect_timeout": 2}
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db = "up"
    except Exception:
        db = "down"
    finally:
        try:
            engine.dispose()
        except UnboundLocalError:
            pass
    status = "ok" if db == "up" else "degraded"
    return JSONResponse(
        status_code=200 if db == "up" else 503,
        content={"status": status, "components": {"db": db}},
    )

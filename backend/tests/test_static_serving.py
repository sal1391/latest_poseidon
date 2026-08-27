"""Phase 14 Task 5 (doc 07 section 2): the single-origin production shape --
one container serving the built SPA and the API from the same origin, with
Caddy in front of it rather than a second static host.

The whole surface under test is three lines in ``api/app.py``:

    if settings.static_dir:
        app.mount("/", StaticFiles(directory=..., html=True), name="spa")

which is deceptively small for how badly it can go wrong. ``Mount("/")``
matches EVERY path there is, and Starlette's router returns the FIRST route
whose ``matches()`` reports ``Match.FULL`` -- so a mount registered one line
too early silently swallows the entire API, and nothing else in this suite
would notice: every existing test builds an app with ``static_dir`` unset,
where no mount exists at all. That is why the tests below are written in
symmetric pairs rather than as a single happy-path check:

- the SPA half (``/`` serves ``index.html``, a hashed asset serves), and
- the must-not-shadow half (``/api/me`` and ``/health/live`` still resolve
  to the API, with the mount present in the very same app),

plus the negative (``static_dir`` unset -> no ``spa`` mount at all, so local
dev and every existing test keep today's behavior with the Vite proxy) and a
structural pin that the mount does not perturb the architecture-fitness
route sweep (``tests/test_architecture_fitness.py``).

**No deep-link fallback is asserted, because none exists.** ``html=True``
serves ``index.html`` for ``/`` and for a directory request; an unknown path
404s. That is correct TODAY: the frontend has no client-side router (a
single-screen SPA), so no URL other than ``/`` is ever a legitimate entry
point. Whoever adds react-router is the one who has to add the catch-all
fallback -- ``api/app.py``'s mount comment names the same seam.
"""

import httpx
import pytest

from poseidon.core.config import Settings

# Content pinned by value, not merely by status code: a 200 whose body is
# some other file (or Starlette's own directory listing) would pass a
# status-only assertion while serving the wrong thing entirely.
_INDEX_HTML = "<!doctype html><html><head><title>poseidon-spa-fixture</title></head></html>"
_ASSET_JS = "export const marker = 'poseidon-asset-fixture';\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def spa_dir(tmp_path):
    """A tmp directory shaped like ``frontend/dist``: an ``index.html`` plus
    one hashed asset under ``assets/`` -- the two request shapes a real
    browser makes against this mount on first load."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text(_ASSET_JS, encoding="utf-8")
    return dist


def _build_app(**overrides):
    """The same lazy-DSN app shape ``tests/test_health.py`` builds: nothing
    here ever connects, and ``create_app`` is lazy about the database by
    design (``core/db.py``'s own module docstring)."""
    from poseidon.api.app import create_app

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/void",
        s3_bucket="poseidon-artifacts",
        **overrides,
    )
    return create_app(settings)


def _mount_names(app) -> list[str]:
    """Every named route on ``app.routes`` -- a name scan rather than a path
    scan, since ``Mount("/")``'s path is the same ``"/"`` a legitimate route
    could also carry; ``name="spa"`` is what identifies THIS mount."""
    return [getattr(route, "name", None) for route in app.routes]


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# The SPA half.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_index_html_is_served_at_root(spa_dir):
    """``html=True``'s whole reason for existing: a bare ``GET /`` returns
    the built ``index.html`` rather than a 404 or a directory listing."""
    app = _build_app(static_dir=str(spa_dir))
    r = await _get(app, "/")
    assert r.status_code == 200
    assert "poseidon-spa-fixture" in r.text


@pytest.mark.anyio
async def test_hashed_asset_is_served(spa_dir):
    """The second request a browser makes on first load -- Vite emits every
    JS/CSS bundle under ``assets/`` with a content hash in the filename."""
    app = _build_app(static_dir=str(spa_dir))
    r = await _get(app, "/assets/app-abc123.js")
    assert r.status_code == 200
    assert "poseidon-asset-fixture" in r.text


# ---------------------------------------------------------------------------
# The must-not-shadow half: the SAME app shape, asked for API paths.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_me_still_resolves_to_the_api_with_the_mount_present(spa_dir):
    """``GET /api/me`` is the SPA's own boot call (``api/auth.py``'s
    ``get_me``). If the mount ever registers before the routers, this
    returns StaticFiles' 404 instead of the identity envelope -- the exact
    failure ordering the mount's placement exists to prevent."""
    app = _build_app(static_dir=str(spa_dir))
    r = await _get(app, "/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["identity_mode"] == "disabled"
    assert "sub" in body


@pytest.mark.anyio
async def test_health_live_still_resolves_to_the_api_with_the_mount_present(spa_dir):
    """The other half of the pair: ``/health/*`` is the load-balancer/
    orchestrator surface (``api/health.py``), and a mount that shadowed it
    would fail every probe on the box while the SPA still looked fine."""
    app = _build_app(static_dir=str(spa_dir))
    r = await _get(app, "/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# The negative, and the structural pin.
# ---------------------------------------------------------------------------


def test_no_spa_mount_when_static_dir_is_unset():
    """Local dev and every pre-existing test in this suite: ``static_dir``
    defaults to ``None``, nothing is mounted, and the Vite dev proxy keeps
    serving the frontend exactly as it does today."""
    app = _build_app()
    assert "spa" not in _mount_names(app)


def test_spa_mount_is_present_and_named_when_static_dir_is_set(spa_dir):
    """The symmetric other half of the negative above -- without it, a
    typo'd mount name (or a mount that stopped being registered at all)
    would leave the negative passing vacuously forever."""
    app = _build_app(static_dir=str(spa_dir))
    assert "spa" in _mount_names(app)


def test_spa_mount_is_registered_last(spa_dir):
    """Starlette's router returns the first FULL match, and ``Mount("/")``
    matches every path there is -- so "after every router" is not a style
    preference, it is the entire correctness argument for the two
    must-not-shadow tests above. Pinned structurally as well as
    behaviorally: a future ``include_router`` appended below the mount would
    be dead code that the HTTP tests could not distinguish from a routing
    accident."""
    app = _build_app(static_dir=str(spa_dir))
    assert _mount_names(app)[-1] == "spa"


def test_route_sweep_floor_still_passes_with_the_mount_present(spa_dir):
    """``tests/test_architecture_fitness.py``'s ``_API_ROUTE_COUNT_FLOOR``
    (Phase 14 Task 4) counts ``/api/*`` routes via ``_iter_api_route_
    contexts``, which walks ``app.routes`` for FastAPI's private
    ``_IncludedRouter`` wrappers. A ``Mount`` is neither an ``APIRoute`` nor
    an ``_IncludedRouter``, so it contributes nothing to that walk and
    cannot inflate or deflate the count -- that is the assumption this test
    pins, in this file, so a Starlette upgrade that changed it fails here
    (next to the mount that depends on it) rather than as a mystifying
    off-by-one in the fitness file."""
    from starlette.routing import Mount

    from tests.test_api_auth import _iter_api_route_contexts
    from tests.test_architecture_fitness import _API_ROUTE_COUNT_FLOOR

    app = _build_app(static_dir=str(spa_dir), chat_mode="live", deploy_mode="local")

    spa = [route for route in app.routes if getattr(route, "name", None) == "spa"]
    assert len(spa) == 1
    assert isinstance(spa[0], Mount)

    swept = [ctx.path for ctx in _iter_api_route_contexts(app)]
    assert sum(1 for path in swept if path.startswith("/api/")) >= _API_ROUTE_COUNT_FLOOR

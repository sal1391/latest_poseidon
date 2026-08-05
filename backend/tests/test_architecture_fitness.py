"""Architecture-fitness invariants (P9 Task 3-M4 / P10 M3-M4): a handful of
properties about this codebase's own SHAPE -- not its behavior for any
particular input -- that nothing else in the suite would notice regressing
until much later: a silent refactor that drops a route from a hand-swept
list, a formatting drift nobody looks at until CI flags it, an error
message that stops naming the variable an operator actually needs to fix.

**Fragility registry (the carryforward's disclosed deviation).** The P9/P10
carryforward asked for ONE file holding every fragile white-box test in
this codebase. This file is not that: two more fragile white-box tests
already exist elsewhere, each pinned by a fixture stack this module would
have to duplicate wholesale -- not merely import -- to relocate here. That
is the deviation, disclosed rather than silently taken:

- ``tests/test_history_cutover.py``'s
  ``test_terminal_frame_is_queued_only_after_the_assistant_row_is_persisted``
  (decorated at line 771, defined at line 773) -- the persist-before-
  terminal-frame call-order proof. It needs a real, reachable Postgres
  (the ``pg_database_url`` fixture) and a monkeypatched
  ``UserHistory.write_assistant_message``/``queue.Queue.put`` pair recording
  relative call order; that whole rig is specific to that file's ``@pytest.
  mark.pg`` suite and has no reason to move.
- ``tests/test_api_auth.py``'s route-classification sweep: the
  ``_iter_api_route_contexts`` helper (line 1530) walks every mounted
  router via FastAPI's own ``_IncludedRouter.effective_route_contexts()``,
  and ``test_every_api_route_is_either_api_me_or_guarded_by_require_sales``
  (line 1579) asserts every non-``/api/me`` route carries ``require_sales``.
  Test 2 below IMPORTS ``_iter_api_route_contexts`` rather than re-deriving
  that FastAPI-internals walk, but the classification test itself stays in
  test_api_auth.py: it needs that file's own ``_mock_app``/``_live_app``/
  Auth0 JWKS fixture stack to build the two app shapes it sweeps.

A reader adding a new shape-level invariant to this codebase should check
both locations above before writing a third copy of similar plumbing --
between this module's docstring and those two files, that is the complete,
current set of "fragile white-box test" locations this codebase carries.

Contents of this file (P9 T3-M4 / P10 M3-M4, one test each):

1. No module under ``poseidon/core`` imports ``fastapi`` or ``starlette``
   (an ``ast`` walk, not an import -- importing the modules under test
   would defeat the point of proving they carry no such import themselves).
2. The route sweep above never silently drops routes: a floor on the
   swept ``/api/*`` count, imported rather than duplicated (see the
   registry above).
3. A malformed ``DATABASE_URL`` fails with a message actionable enough to
   fix without reading this module's source (P10 M3).
4. ``ruff format --check`` over ``backend/poseidon`` (P10 M4) -- conditionally
   armed; see that test's own docstring for which branch this file shipped
   with and why.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_api_auth import _iter_api_route_contexts

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_CORE_DIR = _BACKEND_DIR / "poseidon" / "core"

_FORBIDDEN_TOP_LEVEL_MODULES = {"fastapi", "starlette"}


def _imported_top_level_modules(tree: ast.Module) -> set[str]:
    """The set of top-level module names one module's ``import``/``from
    ... import`` statements name -- ``import a.b.c`` and ``from a.b import
    c`` both contribute only ``"a"``, since that is the granularity the
    invariant below cares about (importing ANYTHING from the ``fastapi`` or
    ``starlette`` package at all is the violation, not a specific symbol)."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # node.module is None for a relative `from . import x` -- never
            # fastapi/starlette, which are always absolute imports.
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def test_core_never_imports_fastapi_or_starlette():
    """P9 T3-M4: ``poseidon/core`` is the web-framework-free layer every
    provider/store/orchestrator module lives in (see e.g. ``core/identity.
    py``'s own module docstring: "Providers never import FastAPI" -- the
    ONE adapter is ``api/app.py``'s middleware, deliberately outside this
    tree). Recon for this task found zero real hits (``core/identity.py``
    mentions ``starlette.requests.Request`` only in a docstring sentence,
    never in an actual import statement) -- this test pins that today, with
    an ``ast`` walk rather than a substring grep, so a docstring mentioning
    either package can never trip it and only a genuine import can.
    ``anyio``/``httpx`` imports (``core/db.py``, ``core/identity_auth0.py``,
    ...) are fine and expected: async primitives and an HTTP client carry no
    web-framework coupling of their own."""
    offenders: dict[str, set[str]] = {}
    for path in sorted(_CORE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hit = _imported_top_level_modules(tree) & _FORBIDDEN_TOP_LEVEL_MODULES
        if hit:
            offenders[str(path.relative_to(_CORE_DIR))] = sorted(hit)

    assert not offenders, (
        f"poseidon/core modules importing fastapi/starlette: {offenders} -- "
        "core must stay web-framework-free; see core/identity.py's own "
        "module docstring for why."
    )


# Provenance for the floor below: measured directly, this task's own
# implementation run (2026-08-05), via
#   Settings(chat_mode="live", deploy_mode="local", ...); create_app(...)
#   sum(1 for ctx in _iter_api_route_contexts(app) if ctx.path.startswith("/api/"))
# chat_mode="live" + deploy_mode="local" mounts live_chat.router AND
# dev_runner.router (plus auth.router/health.router, mounted unconditionally)
# together -- the single app shape that sweeps the MOST /api/* routes of any
# one this codebase builds (mock_chat.router is mutually exclusive with
# live_chat.router; deploy_mode != "local" never mounts dev_runner.router at
# all) -- and it counted 17. A refactor that silently drops a route from
# what _iter_api_route_contexts sees (P9's own re-review feared exactly this
# failure mode) now fails loudly instead of the count quietly shrinking.
_API_ROUTE_COUNT_FLOOR = 17


def _app_for_route_sweep():
    """Builds the ``chat_mode="live"``, ``deploy_mode="local"`` app shape
    the floor above was measured against -- see that comment for why this
    specific shape, not ``_mock_app``/``_live_app`` from test_api_auth.py,
    which are tied to that file's own Auth0/SPCS fixture overrides this test
    has no need of. The placeholder DSN is never connected to: ``build_
    engine``/``create_app`` are both lazy (core/db.py's own module
    docstring), and this offline suite already tolerates the resulting
    ``boot_privileges_unchecked`` warning from Phase 14 Task 3's boot probe
    (see that task's own report)."""
    from poseidon.api.app import create_app
    from poseidon.core.config import Settings

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/void",
        s3_bucket="poseidon-artifacts",
        llm_mode="stub",
        llm_profile="bedrock",
        chat_mode="live",
        deploy_mode="local",
    )
    return create_app(settings)


def test_swept_api_route_count_has_a_floor():
    """P9 T3-M4 / I-3's own re-review concern: a refactor that silently
    stops mounting a router (or drops it from whatever ``_IncludedRouter``
    walk a future change makes) shrinks this count instead of vanishing
    unnoticed. Imports ``_iter_api_route_contexts`` from test_api_auth.py
    rather than re-deriving its FastAPI-internals walk -- see this module's
    own docstring (the fragility registry) for why that helper's home stays
    there while this test only borrows it."""
    app = _app_for_route_sweep()
    count = sum(1 for ctx in _iter_api_route_contexts(app) if ctx.path.startswith("/api/"))
    assert count >= _API_ROUTE_COUNT_FLOOR


def test_malformed_database_url_error_names_database_url_and_expected_form():
    """P10 M3. RED probe (this task's implementation run, captured before
    core/db.py was touched): ``build_engine("not-a-dsn")`` raised
    ``sqlalchemy.exc.ArgumentError("Could not parse SQLAlchemy URL from
    given URL string")`` -- a generic parser complaint naming neither the
    ``DATABASE_URL`` environment variable nor what a valid value looks
    like. ``core/db.py``'s ``build_engine`` now wraps that exact exception
    in a re-raise naming both (see its own docstring's "Phase 14 Task 4"
    section); this test pins the fixed behavior GREEN."""
    from poseidon.core.db import build_engine

    with pytest.raises(Exception) as exc_info:
        build_engine("not-a-dsn")

    message = str(exc_info.value)
    assert "DATABASE_URL" in message
    assert "postgresql" in message  # the hint of the expected form


@pytest.mark.skip(
    reason="armed after the repo-wide ruff-format commit (blocked-on-Carlos pile item)"
)
def test_ruff_format_check_passes_over_poseidon():
    """P10 M4. Conditional arming per this task's own brief: manually run
    before this file was written (2026-08-05), ``ruff format --check
    poseidon`` from ``backend/`` reported 19 files would be reformatted, 112
    already formatted -- the tree is NOT clean. The repo-wide reformat is a
    separate, Carlos-owned pile item this task must not perform (never
    format the tree to make a test pass); this test therefore ships
    skip-marked with the exact reason text the brief specifies, so it is
    discoverable and ready the moment that repo-wide commit lands, rather
    than silently absent. Un-skip it then; no other change to this test
    should be needed."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", "poseidon"],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

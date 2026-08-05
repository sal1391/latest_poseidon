"""Phase 13 Task 3 (doc 05 section 5, doc 01 section 9): the HTTP surface a
future frontend settings panel (Task 5) calls to read/write a user's own
system instruction (Task 1's ``ProfileStore``) and read/write/version/
restore their memory document (Task 1's ``MemoryStore``). Does NOT touch
``core/chat/orchestrator.py`` or the chat flow at all -- Tasks 1-2 already
wired real instruction/memory injection and the outbox touch hook into a
live turn; this task is purely a new, focused route module (this codebase's
``live_chat.py``/``turns.py`` precedent for a single-concern
``APIRouter(prefix="/api", tags=[...])``) plus mounting it.

Mounted only under ``chat_mode == "live"`` (``api/app.py``'s own
``_wire_live_chat``), alongside ``live_chat.router``/``turns.router``,
never unconditionally in ``create_app`` the way ``auth.router`` (``GET
/api/me``) is: the two stores every route below reads off ``request.app.
state`` (``profile_store``, ``memory_store``) are constructed there, not
before it runs -- mirrors ``turns.py``'s own identical "mounted only under
chat_mode=='live'" precedent (see that module's own docstring).

Six routes, every one gated by ``require_sales`` (Global Constraints: the
same role every ``/api/conversations*``/``/api/messages*``/``/api/skills``/
``/api/turns/*`` route already requires) via
``dependencies=[Depends(require_sales)]``, the identical per-route pattern
``live_chat.py``/``turns.py`` already use -- and every write goes through
:func:`~poseidon.core.db.rls_transaction` via the stores themselves (Task
1), never called directly from a handler here.

Route handlers are deliberately thin: each is a single call into
``ProfileStore``/``MemoryStore``'s already-reviewed, RLS-scoped methods
(Task 1) -- this module owns none of the personalization logic itself,
only its HTTP shape (status codes, request/response bodies, and the
``MemoryValidationError``/``MemoryTooLarge`` -> RFC-7807 422 mapping via
the existing ``problem()`` helper, the SAME constructor ``live_chat.py``'s/
``auth.py``'s own error responses already render through -- never a
bespoke error shape).

**``GET /api/me/settings`` never 404s; ``GET /api/me/memory`` does.** Not
the same "no data yet" shape by accident: ``UserProfile.get()`` (Task 1)
returns a real default (``{"system_instruction": "", "updated_at": None}``)
because an empty instruction is itself a valid, common state -- there is
no "not found" outcome for settings to have at all. ``UserMemory.
get_current()`` returns ``None`` for a user who has never called
``write_version`` because there IS a genuine "nothing to show" state for
memory -- this route maps that ``None`` to a 404, the one place in this
task's own interface where the null-vs-404 distinction actually matters
(contrast with settings, which is always 200).

**``PUT /api/me/memory`` always writes ``created_by="user"``, never
``"distiller"``.** This route is the user editing their own memory from
the settings surface -- a user-initiated write. ``"distiller"`` is reserved
for Task 4's not-yet-built background worker; nothing in this module ever
passes it.

**404s here use a bare ``HTTPException``, not ``problem()``.** Matches the
established codebase-wide convention for a plain "not found" (``live_chat.
py``'s ``get_messages``/``delete_conversation``, ``turns.py``'s ``get_
turn``) -- only the two store-level VALIDATION exceptions get the RFC-7807
``problem()`` treatment, per this task's own brief.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from poseidon.api.auth import require_sales
from poseidon.core.personalization.memory import (
    MemoryStore,
    MemoryTooLarge,
    MemoryValidationError,
)
from poseidon.core.personalization.profile import ProfileStore
from poseidon.core.skills.result import problem

router = APIRouter(prefix="/api", tags=["personalization"])

# RFC-7807 "title" values for the two store-level validation exceptions --
# pinned, distinct strings so a caller (or a test) can tell the two failure
# modes apart without parsing "detail" text, the same convention every other
# typed-exception mapping in this codebase already follows (_FEEDBACK_NOT_
# APPLICABLE_TITLE, _MALFORMED_CURSOR_TITLE in live_chat.py).
_MEMORY_TOO_LARGE_TITLE = "memory_too_large"
_MEMORY_VALIDATION_TITLE = "memory_invalid"

_UNKNOWN_MEMORY_DETAIL = "no memory version yet"
_UNKNOWN_MEMORY_VERSION_DETAIL = "unknown memory version"


class SettingsBody(BaseModel):
    """``PUT /api/me/settings``'s request body. An empty string is a valid,
    accepted value -- the column's own ``default ''`` (Task 1's migration
    0008) -- never rejected here."""

    system_instruction: str


class MemoryBody(BaseModel):
    """``PUT /api/me/memory``'s request body -- the user's own edited/
    pruned entries list. Each entry's shape (``type``/``statement``/
    ``source_conversation_id``/``at``) is validated by ``UserMemory.
    write_version`` itself (Task 1), not by this model: a loosely-typed
    ``list[dict]`` here keeps this route from duplicating that contract."""

    entries: list[dict]


@router.get("/me/settings", dependencies=[Depends(require_sales)])
def get_settings_route(request: Request) -> dict:
    """``{"system_instruction": str, "updated_at": str | None,
    "memory_max_chars": int}`` -- always 200, even for a caller who has
    never called ``PUT`` (module docstring's "never 404s").

    ``memory_max_chars`` is additive (Task 5's own cap-source-gap
    amendment, commit 5130fee): the frontend settings surface's character-
    budget meter (doc 01 section 9) has to read the SAME cap ``UserMemory.
    write_version`` enforces (``settings.memory_max_chars``, ``core/
    personalization/memory.py``), and this route was the only one in this
    task's own interface that had nowhere to carry it. Read off
    ``request.app.state.settings`` -- the identical shared-app-state read
    ``auth.py``'s own ``get_me`` route already uses for ``identity_mode``
    -- never a value this route computes, caches, or guesses itself.
    ``PUT /me/settings`` below deliberately does NOT gain this field: the
    amendment's sanctioned scope names the GET route only."""
    profile_store: ProfileStore = request.app.state.profile_store
    settings = request.app.state.settings
    return {
        **profile_store.for_user(request.state.user.sub).get(),
        "memory_max_chars": settings.memory_max_chars,
    }


@router.put("/me/settings", dependencies=[Depends(require_sales)])
def put_settings_route(body: SettingsBody, request: Request) -> dict:
    """Upsert this caller's own instruction; returns the same shape
    :func:`get_settings_route` does."""
    profile_store: ProfileStore = request.app.state.profile_store
    return profile_store.for_user(request.state.user.sub).put(body.system_instruction)


@router.get("/me/memory", dependencies=[Depends(require_sales)])
def get_memory(request: Request) -> dict:
    """``{"version", "entries", "created_by", "created_at"}`` for this
    caller's current (newest) version, or 404 if none has ever been
    written (module docstring's "GET /api/me/memory does 404")."""
    memory_store: MemoryStore = request.app.state.memory_store
    current = memory_store.for_user(request.state.user.sub).get_current()
    if current is None:
        raise HTTPException(404, detail=_UNKNOWN_MEMORY_DETAIL)
    return current


@router.put("/me/memory", dependencies=[Depends(require_sales)])
def put_memory(body: MemoryBody, request: Request) -> dict:
    """Write a new memory version from this caller's own edited entries
    list, always ``created_by="user"`` (module docstring). 200 with the new
    version's dict on success; 422 (RFC-7807, via ``problem()``) when
    ``UserMemory.write_version`` rejects the candidate entries as
    malformed (``MemoryValidationError``) or too large once rendered
    (``MemoryTooLarge``) -- both raised, and both mapped here, strictly
    BEFORE any row is inserted (Task 1's own module docstring), so a
    rejected write never partially lands.

    Returns a plain ``dict`` on the success path and a ``JSONResponse`` on
    either error path -- FastAPI passes any ``Response`` instance a handler
    returns straight through, bypassing this function's own ``-> dict``
    annotation, the identical pattern ``live_chat.py``'s own
    ``upsert_feedback`` already establishes for a route with more than one
    possible response shape.
    """
    memory_store: MemoryStore = request.app.state.memory_store
    user_memory = memory_store.for_user(request.state.user.sub)
    try:
        return user_memory.write_version(body.entries, created_by="user")
    except MemoryTooLarge as exc:
        return JSONResponse(
            status_code=422, content=problem(422, _MEMORY_TOO_LARGE_TITLE, str(exc))
        )
    except MemoryValidationError as exc:
        return JSONResponse(
            status_code=422, content=problem(422, _MEMORY_VALIDATION_TITLE, str(exc))
        )


@router.get("/me/memory/versions", dependencies=[Depends(require_sales)])
def list_memory_versions(request: Request) -> list[dict]:
    """``[{"version", "created_by", "created_at", "entry_count"}]``, newest
    first -- the settings surface's version-history list. An empty list for
    a caller who has never written a version (never a 404 -- an empty list
    is the honest, correct answer to "list my versions" when there are
    none, unlike ``GET /api/me/memory``'s own "show me the current one"
    question, which has no answer at all in that case)."""
    memory_store: MemoryStore = request.app.state.memory_store
    return memory_store.for_user(request.state.user.sub).list_versions()


@router.post("/me/memory/versions/{version}/restore", dependencies=[Depends(require_sales)])
def restore_memory_version(version: int, request: Request) -> dict:
    """Append a new version carrying ``version``'s own entries verbatim,
    attributed ``created_by="user"`` regardless of who authored the
    version being restored (``UserMemory.restore``'s own contract, Task
    1). 404 when ``version`` does not exist FOR THIS CALLER -- ``restore``
    raises ``LookupError`` for both a genuinely unknown version number and
    one that belongs to a different user (RLS's own owner-scoped query
    inside ``restore`` makes the two indistinguishable, by design, the same
    "unknown and foreign collapse into the same 404" discipline every other
    RLS-scoped lookup in this codebase already follows)."""
    memory_store: MemoryStore = request.app.state.memory_store
    user_memory = memory_store.for_user(request.state.user.sub)
    try:
        return user_memory.restore(version)
    except LookupError as exc:
        raise HTTPException(404, detail=_UNKNOWN_MEMORY_VERSION_DETAIL) from exc


__all__ = [
    "MemoryBody",
    "SettingsBody",
    "get_memory",
    "get_settings_route",
    "list_memory_versions",
    "put_memory",
    "put_settings_route",
    "restore_memory_version",
    "router",
]

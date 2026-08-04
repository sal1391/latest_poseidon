from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from poseidon.api import auth, dev_runner, health, live_chat, me, mock_chat, turns
from poseidon.core.artifacts import ArtifactStore
from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.chat.feedback import FeedbackStore
from poseidon.core.chat.history import HistoryStore
from poseidon.core.config import Settings, get_settings
from poseidon.core.data.synthetic_client import SyntheticDataClient
from poseidon.core.db import build_engine
from poseidon.core.identity import AuthError, resolve_provider
from poseidon.core.llm.bedrock import BedrockProvider
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.obs import configure_json_logging, get_logger, new_trace_id, trace_id_var
from poseidon.core.personalization.memory import MemoryStore
from poseidon.core.personalization.outbox import OutboxStore
from poseidon.core.personalization.profile import ProfileStore
from poseidon.core.runlog import RunLogWriter
from poseidon.core.skills.registry import SkillRegistry
from poseidon.mcp.registry import ToolServerRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with: python -m uvicorn poseidon.api.app:create_app --factory"""
    # Phase 11 Task 2 (doc 06 section 3): configured BEFORE anything else in
    # this function runs, so even the very first boot-time log call this
    # process makes (resolve_provider's own "identity mode: ..." line, two
    # statements below) already goes out as one JSON line. Idempotent (see
    # obs.py's own docstring) -- calling this once per create_app() call
    # (once per app/process in production; once per test in this suite) is
    # cheap and never double-registers a handler.
    configure_json_logging()
    app = FastAPI(title="Poseidon API", version="0.1.0")
    app.state.settings = settings or get_settings()

    # Phase 9 Task 1 (doc 05 section 2, decision D22): resolved ONCE here,
    # at boot, so a misconfigured or not-yet-implemented identity_mode
    # fails fast before the app ever accepts a request -- see core/identity.
    # py's own resolve_provider docstring. Every request's identity
    # (request.state.user) is resolved against this ONE provider instance
    # by the middleware installed right below.
    app.state.identity_provider = resolve_provider(app.state.settings)
    _install_identity_middleware(app)

    # Phase 9 Task 2 (Global Constraints): registered here so every
    # AuthError (a provider's resolve, via current_user; require_sales's
    # own 403) and every RateLimitExceeded (the chat-send rate limiter)
    # renders the SAME pinned RFC-7807 body, from the ONE place either
    # mapping happens (api/auth.py's own auth_error_response/
    # rate_limit_exceeded_response -- see that module's docstring).
    app.add_exception_handler(AuthError, auth.auth_error_response)
    app.add_exception_handler(auth.RateLimitExceeded, auth.rate_limit_exceeded_response)

    # CORS (Global Constraints: explicit allowlist, never * with
    # credentials -- core/config.py's own cors_allow_origins validator
    # already refuses "*" at boot). Added AFTER the identity middleware
    # above so it wraps OUTERMOST (Starlette applies user middleware in
    # LAST-added-is-outermost order): a preflight OPTIONS request is
    # answered here directly, before the identity middleware ever runs,
    # and every response this app produces -- including a 401/403/429 from
    # deep inside a route's own dependency resolution -- still passes back
    # through this layer and picks up the right CORS headers on the way
    # out. allow_methods/allow_headers stay wildcarded: Starlette's own
    # CORSMiddleware expands "*" methods to the concrete method list and,
    # for headers, mirrors back the caller's actual requested headers
    # rather than ever sending a literal "*" -- neither is the "* origin
    # with credentials" anti-pattern the allowlist above exists to forbid
    # (verified by reading CORSMiddleware's own source).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app.state.settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Phase 11 Task 2 (doc 06 section 3): installed LAST among this
    # function's app.add_middleware()/@app.middleware("http") calls, so it
    # is the OUTERMOST layer in the chain (Starlette's own last-added-is-
    # outermost order -- see _install_identity_middleware's own docstring,
    # which documents the identical rule for CORS-over-identity). Being
    # outermost means its pre-call_next code -- minting the trace id and
    # setting the contextvar -- runs BEFORE the CORS preflight short-
    # circuit and before identity middleware's own per-request logic, so
    # EVERY response this app ever returns (a 2xx from a route, a 401/403
    # from identity, a 429 from the rate limiter, or a CORS preflight
    # answered before any route runs) carries the same X-Trace-Id header,
    # and anything logged anywhere further in is attributed to that same id.
    _install_trace_middleware(app)

    app.include_router(health.router)
    # Phase 9 Task 1: GET /api/me -- mounted unconditionally, like health.
    # router, not only under chat_mode="live". See api/auth.py's own module
    # docstring for why identity discovery cannot be gated behind chat_mode.
    app.include_router(auth.router)

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
        #
        # Phase 9 final-review fix wave (I-2): guarded by require_sales the
        # SAME way dev_runner.router already is below -- mock_chat.py
        # itself stays untouched (its own "test-only" plan constraint).
        # Before this fix, NOTHING stopped this router from being mounted
        # wide open under a non-disabled identity_mode: auth0/spcs_ingress
        # both default to chat_mode="mock" too, unless an operator sets
        # CHAT_MODE=live, so an auth0 deploy that forgot that one env var
        # served six unauthenticated /api/conversations*//api/messages*
        # routes. In disabled mode -- every existing test/env --
        # DISABLED_DEFAULT_USER already carries Poseidon:Sales, so this
        # guard is a no-op there: every existing mock test keeps passing
        # unchanged (tests/test_mock_chat.py's own `app` fixture builds
        # default, disabled-mode Settings).
        app.include_router(mock_chat.router, dependencies=[Depends(auth.require_sales)])

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
        # Phase 9 Task 2 (Global Constraints' enforcement scope): every
        # /api/dev/* route requires Poseidon:Sales. Guarded at inclusion
        # time, for the whole router at once, rather than editing
        # dev_runner.py itself -- FastAPI applies an include_router(...,
        # dependencies=...) to every route already registered on that
        # router, so this is the one place Phase 10 will mirror for its
        # own new conversations/messages router (this task's own brief:
        # "export the dependency cleanly so Phase 10 can attach it").
        app.include_router(dev_runner.router, dependencies=[Depends(auth.require_sales)])
    return app


# Phase 9 final-review fix wave (I-4): header names this app treats as
# identity-bearing -- see _install_identity_middleware's own docstring for
# why a duplicate occurrence of either is rejected before the lowercase
# mapping collapse, and why X-Dev-User is deliberately excluded.
_IDENTITY_BEARING_HEADERS = ("authorization", "sf-context-current-user")
_DUPLICATE_HEADER_TITLE = "duplicate identity header"

# Phase 9 final-review fix wave (I-1): the ONE generic problem body a
# caller ever sees for a provider failure that is not a pinned AuthError --
# see _install_identity_middleware's own docstring for the full
# containment contract these two constants render.
_IDENTITY_UNAVAILABLE_TITLE = "identity_unavailable"
_IDENTITY_UNAVAILABLE_DETAIL = "the identity provider failed unexpectedly; try again shortly"


def _first_duplicate_identity_header(request: Request) -> str | None:
    """The first header name in :data:`_IDENTITY_BEARING_HEADERS` that
    ``request.headers.getlist`` finds more than once on this request, or
    ``None`` if none repeat. ``Headers.getlist`` matches names case-
    insensitively (Starlette's own implementation), the same case-
    insensitivity every provider's own header lookup already relies on, so
    ``sf-context-current-user`` and ``Sf-Context-Current-User`` (or any
    other casing) count as the SAME header.
    """
    for name in _IDENTITY_BEARING_HEADERS:
        if len(request.headers.getlist(name)) > 1:
            return name
    return None


def _install_trace_middleware(app: FastAPI) -> None:
    """Phase 11 Task 2 (doc 06 section 3): one ``trace_id`` per HTTP
    request, minted here and stamped into ``obs.trace_id_var`` for the
    ENTIRE lifetime of the request. See :func:`create_app`'s own call site
    comment for why this is installed LAST (making it the OUTERMOST
    middleware) and ``core/obs.py``'s own module docstring ("Propagation,
    not threading") for how a value set here stays visible to a
    ``get_logger(...)``/``span(...)`` call made from a worker thread this
    request later spawns (``anyio.to_thread.run_sync`), with no explicit
    parameter threaded through any function signature in between.

    The contextvar is reset in a ``finally``, not left dangling: without
    the reset, a value set for THIS request's context would look, to
    anything reusing the same underlying context after the request ends,
    like it was still "this request" -- resetting is what makes the
    contextvar safe to reuse across the process's entire request lifetime,
    request after request.
    """

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        trace_id = new_trace_id()
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["X-Trace-Id"] = trace_id
        return response


def _install_identity_middleware(app: FastAPI) -> None:
    """Phase 9 Task 1 (doc 05 section 2, decision D22): resolve ``request.
    state.user`` for EVERY request, before any route handler or dependency
    runs -- the one seam everything below it (``live_chat.py``'s
    ``user_sub``, the orchestrator's ``SkillContext.user``, ``GET /api/me``)
    reads instead of ever touching an ``IdentityProvider`` or a raw header
    itself (provider-blindness -- see ``core/identity.py``'s own module
    docstring). Cheap in disabled mode: ``DisabledProvider.resolve`` is a
    dict lookup plus a regex check, no I/O -- paying this on every request,
    including ``/health/*``, is the honest cost of "one seam, no
    exceptions" rather than a per-route opt-in that some path could
    silently forget.

    Headers are lowercased ONCE, here -- the one and only adapter between a
    real ``Request`` and the plain ``Mapping[str, str]`` every
    ``IdentityProvider.resolve`` accepts, so a provider module never needs
    to import FastAPI/Starlette at all (``core/identity.py``'s own "providers
    never import FastAPI" seam).

    ``AuthError`` (a provider's typed failure -- see ``core/identity.py``)
    is caught HERE (Phase 9 Task 2, alongside ``Auth0Provider``, the first
    provider that can actually raise it) and recorded on ``request.state.
    auth_error`` rather than re-raised immediately: re-raising from the
    middleware would fail the WHOLE request before any route even ran,
    which would break "``/health/*`` stays open" (Global Constraints) the
    moment ``auth0`` mode saw a health-check request with no bearer token
    -- the common case for any load balancer/orchestrator probe. Instead,
    the request proceeds normally (``call_next`` still runs); only a route
    that actually DEPENDS on identity (``api/auth.py``'s ``current_user``/
    ``require_sales``) reads ``request.state.auth_error`` back and turns it
    into the pinned 401/403 response -- see ``current_user``'s own
    docstring for the three-way branch that implements this.

    Phase 9 final-review fix wave adds two more seams to this SAME one
    adapter, both BEFORE ``provider.resolve`` is ever called with a
    collapsed header mapping:

    - **I-4 (duplicate identity-bearing headers).** ``request.headers`` is
      a multidict; the naive ``{name.lower(): value for name, value in
      request.headers.items()}`` collapse below keeps only the LAST
      occurrence of a repeated header name. For ``Authorization``/
      ``Sf-Context-Current-User`` specifically, "last one wins" is a trust
      hazard, not a cosmetic ambiguity: if a caller can append a second
      occurrence of a header a trusted edge (the SPCS ingress) is assumed
      to set exactly once, they can override the platform's own value with
      their own. Checked via ``Headers.getlist`` (case-insensitive,
      matching every provider's own header lookup) before the collapse
      even runs -- more than one occurrence of either name records the
      SAME kind of ``AuthError`` a bad credential would (401), never a
      500, and ``provider.resolve`` never even sees the ambiguous mapping.
      ``X-Dev-User`` is deliberately NOT included: ``DisabledProvider``'s
      whole design point is "never block local dev, ever" (its own
      docstring), and it is not a platform trust boundary the way the
      other two headers are (disclosed judgment call, this task's own wave
      report) -- and ``Authorization`` is included even though the
      review's own I-4 finding only named ``Sf-Context-Current-User``:
      no legitimate client ever sends two, and the same last-one-wins
      ambiguity applies regardless of which header carries it (also
      disclosed there).
    - **I-1 (provider failures beyond AuthError).** ``Auth0Provider.
      resolve`` (or any future provider) can raise something that is NOT
      an ``AuthError`` -- a JWKS fetch that times out or 5xxs, a JWK whose
      ``kty`` is not RSA colliding with a requested ``kid`` (M-10), or any
      other bug. Before this fix, any such exception escaped this
      middleware entirely and became an opaque 500 on EVERY route,
      including ``/health/*`` -- breaking this very function's own
      "``/health/*`` stays open" promise the moment a credential was
      merely unverifiable rather than absent. Caught the same way
      ``AuthError`` already is (recorded, never re-raised here), logged
      server-side for operator visibility, and surfaced -- only to a route
      that actually asks -- as the SAME pinned, generic 401 problem
      (``identity_unavailable``) regardless of which underlying exception
      caused it: the caller-actionable answer ("try again; if this
      persists, it is not your credential") is identical either way, and a
      provider staying FastAPI-free (``core/identity.py``'s own seam) means
      this containment has to live here, not in the provider.
    """

    @app.middleware("http")
    async def identity_middleware(request: Request, call_next):
        provider = request.app.state.identity_provider
        duplicate = _first_duplicate_identity_header(request)
        if duplicate is not None:
            request.state.auth_error = AuthError(
                401,
                _DUPLICATE_HEADER_TITLE,
                f"request carried more than one {duplicate!r} header",
            )
            return await call_next(request)
        headers = {name.lower(): value for name, value in request.headers.items()}
        try:
            request.state.user = provider.resolve(headers)
        except AuthError as exc:
            request.state.auth_error = exc
        except Exception as exc:  # noqa: BLE001 - I-1: contain ANY provider failure, never a bare 500
            print(
                f"WARNING: identity provider raised {type(exc).__name__}: {exc} -- "
                "resolving this request to a generic 401 instead of a bare 500",
                flush=True,
            )
            request.state.auth_error = AuthError(
                401, _IDENTITY_UNAVAILABLE_TITLE, _IDENTITY_UNAVAILABLE_DETAIL
            )
        return await call_next(request)


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
    only a packaged YAML file and a prompts directory, ``HistoryStore``/
    ``FeedbackStore`` (Phase 12 Task 1; replaces the in-memory
    ``FeedbackStubStore``) are both explicitly meant to be ONE shared
    instance each (their whole job is being shared, identity-scoped access
    to persisted state across requests -- see ``core/chat/history.py``'s/
    ``core/chat/feedback.py``'s own module docstrings), and
    ``SyntheticDataClient`` holds nothing but a DSN
    string (its own module docstring), so one long-lived instance behaves
    identically to a fresh one per request -- unlike ``dev_runner.py``'s
    per-request construction, there is no connection or other per-call state
    here to keep isolated between requests. ``RoleClient`` registers BOTH
    the stub router (``DevDeterministicRouter``, what actually answers in
    ``LLM_MODE=stub``) and the real Bedrock provider (what answers in
    ``LLM_MODE=live`` with ``llm_profile=bedrock``) -- the two canonical
    shapes ``roles.py``'s own docstring documents; registering both at once
    is harmless (``RoleClient.invoke`` reads exactly one key per call).
    ``tool_registry`` (Phase 7 Task 4) follows the identical "build once,
    share" discipline -- see :func:`_build_tool_registry`.

    **Phase 10 Task 3: ONE Engine per process (doc 08's cutover).** Before
    this task, ``RunLogWriter`` got its own ``create_engine(settings.
    database_url)`` call (:func:`_build_run_log_writer`, removed by this
    task) -- a second, independent connection pool against the exact same
    database ``HistoryStore`` now also needs. :func:`~poseidon.core.db.
    build_engine` is called exactly ONCE here and the SAME ``Engine`` object
    is handed to both ``RunLogWriter`` and ``HistoryStore`` (``health.py``'s
    own throwaway ``/ready`` probe engine is unrelated and untouched -- a
    short-lived probe connection, not a pool this app holds for the process
    lifetime). ``app.state.db_engine`` also keeps a direct handle on it:
    ``live_chat.py``'s feedback routes need one more RLS-scoped query
    (``SELECT 1 FROM messages WHERE id = :id``) that ``UserHistory`` itself
    has no method for (a bare message id, with no conversation id to scope
    it through -- see that module's own docstring for why this is the one
    place this cutover reaches for :func:`~poseidon.core.db.rls_transaction`
    directly rather than going through ``UserHistory``), and doing that
    against a SECOND, independently-built engine would defeat the entire
    point of unifying the pool here.

    **A malformed ``DATABASE_URL`` is now a hard boot failure under
    ``chat_mode="live"`` (a behavior change from ``_build_run_log_writer``'s
    old contract).** ``create_engine`` -- called once via ``build_engine``
    below -- never opens a network connection (engines are lazy, so an
    unreachable-but-well-formed host still builds fine here and only fails
    later, per call), but it DOES eagerly parse its URL argument, so a value
    ``Settings.database_url``'s own "not blank" validator accepts but which
    is not a URL SQLAlchemy can parse at all still raises immediately.
    ``_build_run_log_writer`` used to catch exactly that and log a WARNING,
    booting anyway with ``run_log_writer=None`` -- a deliberate design for
    an OPTIONAL run log. History is not optional anymore: every route
    ``live_chat.py`` serves now reads ``app.state.history_store``
    unconditionally, with no ``is not None`` guard anywhere (unlike
    ``run_log_writer``, which ``execute_turn`` has always treated as
    optional throughout). Catching the same exception here and continuing
    to boot would leave ``app.state.history_store`` unset, which would not
    degrade gracefully the way a missing run log does -- it would 500 the
    very first real request instead of failing loudly at boot, the opposite
    of ``Settings``'s own stated philosophy ("no half-configured server ever
    accepts traffic"). Letting the exception propagate is therefore the
    correct, disclosed choice: a live-chat deploy with a malformed
    ``DATABASE_URL`` now fails ``create_app`` itself, matching that
    philosophy instead of carving out a second, inconsistent exception for
    it.
    """
    settings = app.state.settings
    engine = build_engine(settings.database_url)
    app.state.db_engine = engine
    # app_role is load-bearing, not decorative: omitting it here would
    # silently disable RLS enforcement on this dev database's superuser DSN
    # (core/db.py's own "round-0 correction" -- a Postgres superuser
    # unconditionally bypasses row-level security), exactly the bypass class
    # Phase 10 Task 1/2 closed at the rls_transaction layer. Settings.
    # database_app_role already resolves to None for a real deploy whose DSN
    # authenticates as an ordinary, non-privileged role.
    app.state.history_store = HistoryStore(engine, app_role=settings.database_app_role)
    # Phase 10 Task 3: replaces the Task 5 amendment's in-memory
    # TranscriptStore/ConversationStateStore pair -- see live_chat.py's own
    # module docstring for the full cutover. Phase 12 Task 1: FeedbackStore
    # replaces the in-memory FeedbackStubStore that same cutover introduced
    # here -- migration 0006's persisted message_feedback table is the
    # promise that module's own docstring named. Same app_role rationale as
    # history_store immediately above (this dev database's DATABASE_URL role
    # is a superuser; omitting it would silently disable RLS enforcement for
    # every feedback write this process makes) and the SAME shared engine.
    app.state.feedback_store = FeedbackStore(engine, app_role=settings.database_app_role)
    # Phase 13 Task 1/2: the three personalization stores behind
    # user_profile/user_memory/memory_outbox (migration 0008), mirroring
    # history_store/feedback_store immediately above verbatim (same shared
    # engine, same app_role rationale: this dev database's DATABASE_URL
    # role is a superuser, and omitting app_role would silently disable RLS
    # enforcement for every read/write these three stores make).
    # live_chat.py's own execute_turn(...) call reads these three straight
    # back off app.state (plan amendment, commit bf43d34) -- so a real HTTP
    # turn now actually exercises the real per-turn instruction/memory
    # injection and outbox touch, not just execute_turn's own capability.
    app.state.profile_store = ProfileStore(engine, app_role=settings.database_app_role)
    app.state.memory_store = MemoryStore(engine, app_role=settings.database_app_role)
    app.state.outbox_store = OutboxStore(engine, app_role=settings.database_app_role)
    app.state.role_client = RoleClient(
        settings, providers={"stub": DevDeterministicRouter(), "bedrock": BedrockProvider()}
    )
    prompts_dir = settings.prompts_dir if settings.prompts_dir is not None else DEFAULT_PROMPTS_DIR
    app.state.prompt_registry = PromptRegistry(prompts_dir)
    app.state.data_client = SyntheticDataClient(settings.database_url)
    # Phase 11 Task 1: turn_run/llm_calls/tool_calls join conversations/
    # messages under the same RLS discipline (migration 0005) -- app_role is
    # load-bearing here for the identical reason the history_store
    # construction above already documents (this dev database's DATABASE_URL
    # role is a superuser; omitting it would silently disable RLS
    # enforcement for every run-log write this process makes).
    app.state.run_log_writer = RunLogWriter(engine, app_role=settings.database_app_role)
    print(
        "chat persistence: history store + run-log writer share one Engine "
        "(DATABASE_URL configured)",
        flush=True,
    )
    app.state.tool_registry = _build_tool_registry(settings)
    app.state.chat_rate_limiter = _build_chat_rate_limiter(settings)
    # MalformedCursor (core/chat/history.py) can only ever be raised from a
    # route on THIS router (list_conversations/get_messages), so its handler
    # is registered here, scoped alongside the router it protects, rather
    # than unconditionally at the top of create_app the way AuthError/
    # RateLimitExceeded are (both of those are reachable from routes mounted
    # regardless of chat_mode, e.g. GET /api/me).
    app.add_exception_handler(live_chat.MalformedCursor, live_chat.malformed_cursor_response)
    app.include_router(live_chat.router)
    # Phase 11 Task 3 (doc 01 section 5): the reconnect reconciliation
    # endpoint -- mounted here, alongside live_chat.router, rather than
    # unconditionally in create_app, since it reads app.state.db_engine,
    # which only exists once this function has run (see turns.py's own
    # module docstring).
    app.include_router(turns.router)
    # Phase 13 Task 3 (doc 05 section 5, doc 01 section 9): the settings-
    # panel HTTP surface (GET/PUT /api/me/settings, GET/PUT /api/me/memory,
    # GET /api/me/memory/versions, POST /api/me/memory/versions/{v}/restore)
    # -- mounted here, alongside live_chat.router/turns.router, rather than
    # unconditionally in create_app, since it reads app.state.profile_store/
    # app.state.memory_store, both constructed above in this same function
    # (see me.py's own module docstring).
    app.include_router(me.router)


def _build_chat_rate_limiter(settings: Settings) -> auth.ChatRateLimiter | None:
    """Phase 9 Task 2 (Global Constraints): ``None`` when the resolved
    limit is 0 ("off" -- ``disabled`` mode, by default) rather than a
    ``ChatRateLimiter`` built with zero capacity, which would reject every
    request forever instead of none -- see that class's own docstring for
    why "off" is a distinct code path. ``live_chat.py``'s own chat-send
    route reads this back via ``api/auth.py``'s ``rate_limit_chat_send``
    dependency.
    """
    limit = settings.effective_rate_limit_chat_per_minute
    if limit <= 0:
        return None
    return auth.ChatRateLimiter(limit)


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

    **Phase 11 Task 2 (doc 06 section 3):** this used to be a bare
    ``print(f"research transport: {label}", flush=True)``; it is now one
    structured JSON line through ``core/obs.py`` -- ``label`` (the exact
    pinned text every existing test in this codebase already asserts on,
    e.g. ``"fixture (llm_mode=stub)"``) is content-preserved verbatim as
    ``context["transport"]`` rather than dropped, so
    ``test_live_chat_sse.py``'s own pinned boot-line tests adapt to read it
    from there instead of scanning raw stdout text (disclosed in this
    task's own report).
    """
    if settings.llm_mode == "stub":
        from poseidon.mcp.perplexity.fixture_tool import FixtureResearchTool

        overrides = {"research": FixtureResearchTool()}
        label = "fixture (llm_mode=stub)"
    else:
        overrides = None
        label = f"{settings.tool_transport_perplexity} (llm_mode=live)"
    get_logger("app").info("research_transport_resolved", transport=label)
    return ToolServerRegistry(settings, overrides=overrides)

"""Structured JSON logging, request tracing, and lightweight span timings
(doc 06 section 3): the one place every log line in this codebase that
wants to be machine-readable, not free-form prose, goes through.

Why this module exists (doc 06's own two named lessons): TM1 shipped with
near-zero server logging, and mom-comparison shipped with zero tests --
"nothing watching means nobody finds out" applies to production behavior
just as much as it applies to test coverage. Every line this module emits
is exactly one JSON object with exactly seven keys -- {ts, level, trace_id,
turn_id, component, event, context} (absent values null) -- so
`docker compose logs backend` is grep/jq-able from day one.

**NO OpenTelemetry dependency.** `span()`'s names (`parse`, `route`,
`skill:<id>`, `subskill:<id>`, `llm:<role>`, `db:query`, `ext:perplexity`)
are merely OTel-compatible STRINGS -- chosen so a real OTel exporter could
be bolted on later without renaming anything -- this module imports
nothing beyond the standard library plus this codebase's own uuid7 helper.

**Provider-blind, framework-blind.** This module never imports FastAPI (or
Starlette): `api/app.py`'s own trace middleware is the ONE place an ASGI
`Request`/`Response` ever appears in this system's tracing story. This
module deals only in contextvars, stdlib `logging`, and plain strings --
the same "providers never import FastAPI" seam `core/identity.py` documents
for a different pair of layers, applied here to observability.

**Propagation, not threading.** `trace_id_var` is a `contextvars.
ContextVar` set ONCE per HTTP request, by `api/app.py`'s trace middleware,
and read implicitly by every `get_logger(...)` call this request's call
stack makes -- including a call made from a WORKER THREAD
(`anyio.to_thread.run_sync`, exactly how `api/live_chat.py` runs
`execute_turn`, and exactly how FastAPI itself runs a sync route function
such as `api/dev_runner.py`'s). Both `asyncio.to_thread` and `anyio.
to_thread.run_sync` copy the calling `contextvars.Context` into the worker
thread, so a value `.set()` on the event-loop side is still visible to a
`.get()` call made deep inside a synchronous orchestrator/skill/data-client
call running on that thread, with zero explicit `trace_id` parameter
threaded through any of those functions' own signatures. This is the whole
reason a contextvar is the right tool here, proven end to end (not merely
asserted) by `tests/test_obs_logging.py`'s own worker-thread-crossing
cases.

**`turn_id` stays null in this task.** The seven-key shape reserves a
`turn_id` field, but nothing in Task 2's own sanctioned call sites has a
turn id to `.set()` at the CONTEXTVAR layer the way `trace_id_var` does:
unlike a trace id, there is no "one turn_id per HTTP request" -- a request
may drive zero or one turn, and the id is minted deep inside
`execute_turn`, well after this app's own middleware chain already ran.
A call site that already holds a turn id in scope (`execute_turn`'s own
`parse`/`route` spans, which run after `sink.turn_id` exists) attaches it
as an ordinary named `context` field instead -- see `core/chat/
orchestrator.py`'s own span call sites. A dedicated `turn_id_var` the same
shape as `trace_id_var` is left to whichever future task first needs
turn-level correlation from OUTSIDE a span call's own local scope.

**Why `get_logger` returns a thin wrapper, not a bare `logging.Logger`.**
Every OTHER module in this codebase that already calls `logging.
getLogger(__name__)` (`core/runlog.py`, `api/live_chat.py`, `core/chat/
orchestrator.py`, ...) keeps doing exactly that, completely unaffected by
this module: those loggers are never touched, reformatted, or routed
through `configure_json_logging`'s handler. `get_logger(component)` is a
separate, opt-in seam for NEW, observability-aware call sites (the two
converted boot lines, `span()` itself) -- `component` is stamped
explicitly (`extra={"component": ...}`) rather than derived from a
logger's dotted `__name__`, so the JSON `component` field is always
exactly the string a caller passed, never a guess based on module path.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic

from poseidon.core.util.uuid7 import uuid7

# One trace id per HTTP request (doc 06 section 3), set by api/app.py's
# trace middleware and read by every get_logger(...)/span() call this
# request's call stack makes -- see the module docstring's "Propagation,
# not threading". Default None: a log line emitted outside any HTTP
# request (a script, a REPL, an offline test that never went through the
# trace middleware) is honestly traceless, not a bug to paper over.
trace_id_var: ContextVar[str | None] = ContextVar("poseidon_trace_id", default=None)

# See the module docstring's "turn_id stays null in this task" -- reserved
# for a future task; nothing in Task 2's own scope ever calls .set() here,
# so this always reads back None.
_turn_id_var: ContextVar[str | None] = ContextVar("poseidon_turn_id", default=None)

# The one internal stdlib logger every _ComponentLogger funnels through.
# Deliberately NOT the real root logger -- see configure_json_logging's own
# docstring for why every pre-existing logging.getLogger(__name__) call
# site in this codebase (and every third-party library's own logging) must
# stay completely unaffected by this module ever being configured.
_INTERNAL_LOGGER_NAME = "poseidon.obs"


def new_trace_id() -> str:
    """One fresh trace id: ``uuid7().hex`` (32 lowercase hex characters, no
    dashes). Reuses this codebase's own post-Phase-10 id generator rather
    than ``uuid4().hex`` purely so there is one fewer id-minting convention
    to carry -- time-ordering itself buys a trace id nothing, since nothing
    indexes on it the way ``messages``/``conversations`` do."""
    return uuid7().hex


def _iso8601_utc(timestamp: float) -> str:
    """``timestamp`` (a ``time.time()``-style float, e.g. ``LogRecord.
    created``) as ISO-8601 UTC with a literal trailing ``Z`` rather than
    ``+00:00`` -- both are valid ISO-8601; ``Z`` is the more common
    convention in JSON logging and unambiguous either way."""
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


class _JsonFormatter(logging.Formatter):
    """Renders one :class:`logging.LogRecord` as ONE JSON line: exactly the
    seven keys doc 06 section 3 pins, nothing more, absent values null.

    ``ts`` comes from ``record.created`` (when the log CALL happened, not
    when this formatter ran -- the two can differ under a slow handler, and
    ``created`` is the honest one). ``component``/``context`` are read off
    the record's own ``extra`` attributes -- see :class:`_ComponentLogger`,
    the one place that sets them -- falling back to ``record.name`` /
    ``None`` respectively so this formatter never raises even if something
    logs through the internal logger some other way.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": _iso8601_utc(record.created),
            "level": record.levelname,
            "trace_id": trace_id_var.get(),
            "turn_id": _turn_id_var.get(),
            "component": getattr(record, "component", record.name),
            "event": record.getMessage(),
            "context": getattr(record, "context", None) or None,
        }
        return json.dumps(payload, default=str)


class _StdoutHandler(logging.Handler):
    """A :class:`logging.Handler` that looks up ``sys.stdout`` FRESH on
    every ``emit`` instead of capturing it once at construction time (the
    default :class:`logging.StreamHandler` behavior) -- the same property a
    bare ``print(..., flush=True)`` call already has for free, and the one
    property this module's replacement of two such prints must preserve:
    pytest's ``capsys`` fixture substitutes ``sys.stdout`` PER TEST, and a
    handler that captured the real stream once, at the first
    ``configure_json_logging()`` call anywhere in a test session, would
    keep writing to it forever -- invisible to every later test's own
    ``capsys.readouterr()``. Flushes explicitly after every write for the
    identical reason the boot prints this module replaces already pass
    ``flush=True``: a container's stdout is a pipe, not a TTY, and an
    unflushed line can sit in Python's block buffer forever.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 - logging must never crash its caller
            self.handleError(record)


def configure_json_logging() -> None:
    """Attach the JSON handler+formatter to this module's own internal
    logger, exactly once per process. Idempotent by construction (checked
    via ``.handlers`` -- a second call is a silent no-op), so every caller
    (``api/app.py``'s ``create_app``, and any test wanting JSON output
    without building a whole app) can call it unconditionally without
    worrying about double-registered handlers doubling every log line.

    Attached to a DEDICATED logger (``poseidon.obs``), not the real root
    logger, with ``propagate = False`` -- see the module docstring's "Why
    get_logger returns a thin wrapper" for why this deliberately leaves
    every pre-existing ``logging.getLogger(__name__)`` call site in this
    codebase, and every third-party library's own logging (uvicorn,
    sqlalchemy, alembic), completely unaffected.
    """
    logger = logging.getLogger(_INTERNAL_LOGGER_NAME)
    if logger.handlers:
        return
    handler = _StdoutHandler()
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class _ComponentLogger:
    """What :func:`get_logger` hands back: ``.info``/``.warning``/``.error``
    taking an ``event`` string and arbitrary ``**context`` -- the same
    shape :func:`span` itself uses to emit its own line. Not a
    :class:`logging.Logger` subclass on purpose: the public surface here is
    deliberately narrower (three methods, one fixed ``component``), so a
    call site can never reach for ``%``-style positional formatting or any
    other stdlib ``Logger`` feature this seam does not want to support.
    """

    def __init__(self, component: str) -> None:
        self._component = component
        self._logger = logging.getLogger(_INTERNAL_LOGGER_NAME)

    def info(self, event: str, **context: object) -> None:
        self._log(logging.INFO, event, context)

    def warning(self, event: str, **context: object) -> None:
        self._log(logging.WARNING, event, context)

    def error(self, event: str, **context: object) -> None:
        self._log(logging.ERROR, event, context)

    def _log(self, level: int, event: str, context: dict[str, object]) -> None:
        self._logger.log(
            level, event, extra={"component": self._component, "context": context or None}
        )


def get_logger(component: str) -> _ComponentLogger:
    """A logger stamped with ``component`` on every line it emits -- see
    the module docstring's "Why get_logger returns a thin wrapper"."""
    return _ComponentLogger(component)


@contextmanager
def span(name: str, **context: object) -> Iterator[None]:
    """Time the wrapped block and emit ONE log line on exit -- success or
    failure alike (``try/finally``, not a bare fall-through: knowing how
    long a call took before it FAILED is exactly as valuable as knowing how
    long a successful one took, and a ``with span(...):`` block must never
    change whether an exception raised inside it propagates). Spans ADD log
    emission; they never re-time anything this codebase already records --
    ``llm_calls.latency_ms``/``tool_calls.latency_ms`` are written exactly
    as before this function existed.

    ``name`` is logged VERBATIM (the OTel-compatible strings doc 06 section
    3 pins: ``parse``, ``route``, ``skill:<id>``, ``subskill:<id>``,
    ``llm:<role>``, ``db:query``, ``ext:perplexity``) as ``context["name"]``,
    alongside ``context["duration_ms"]`` -- both ADDED to (and, on a literal
    key collision, overriding) whatever ``**context`` the caller passed, so
    the two fields this function itself promises can never be accidentally
    shadowed by a caller's own kwarg. ``component`` is derived from
    ``name``'s segment before its first ``":"`` (``"skill:foo.bar"`` ->
    ``"skill"``; ``"parse"`` has no colon, so it maps to itself) -- a
    simple, deterministic grouping; this contextmanager's fixed two-
    parameter signature (``name``, ``**context``) has no room for a
    separate, explicit component argument.

    ``duration_ms`` is wall-clock (:func:`time.monotonic`, immune to system
    clock adjustments mid-span), floored at 0 -- defensive only:
    ``monotonic()`` never runs backward, so the floor never actually
    triggers in practice, but "non-negative" is this function's own pinned
    contract, not merely an expectation of how ``monotonic`` happens to
    behave today.
    """
    logger = get_logger(name.split(":", 1)[0])
    started = monotonic()
    try:
        yield
    finally:
        duration_ms = max(0, int((monotonic() - started) * 1000))
        full_context = dict(context)
        full_context["name"] = name
        full_context["duration_ms"] = duration_ms
        logger.info("span", **full_context)


__all__ = [
    "configure_json_logging",
    "get_logger",
    "new_trace_id",
    "span",
    "trace_id_var",
]

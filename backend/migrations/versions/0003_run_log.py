"""run log

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29

Creates the three run-log tables (docs/architecture/06-observability.md
section 1): the parent ``turn_run`` row per chat turn -- or per background
LLM run, discriminated by ``kind`` -- and its two append-only children,
``llm_calls`` and ``tool_calls``. Columns, defaults, checks, uniques and
indexes below are a transcription of doc 06 section 1's SQL block: this
migration is the one place that SQL becomes a real schema, so nothing here
is invented or simplified relative to what the doc already specifies.

Unlike migration 0002's ``synthetic`` schema (mock certified-entity data,
deliberately namespaced away from the app's own state), these three tables
are core operational state and live in the default schema -- there is no
"certified vs. operational" boundary to keep them on the other side of.

``conversation_id``/``message_id`` carry no foreign key: doc 06's own SQL
declares none, because the ``conversations``/``messages`` tables they will
eventually reference do not exist yet at this revision (doc 08 assigns them
to Phase 10). Adding a forward reference here would be inventing a
constraint doc 06 does not specify.

Same no-op-on-non-Postgres guard as 0002 (see that module's docstring): this
migration does nothing on SQLite, so ``backend/tests/test_migrations.py``
(``alembic upgrade head`` against a throwaway SQLite database) stays green
without needing SQLite equivalents for ``jsonb``, ``timestamptz``, or
``INSERT ... ON CONFLICT ... RETURNING``.

``id`` columns carry no server-side default: doc 06 marks them "UUIDv7", but
the Python standard library has no UUIDv7 generator (only uuid1/3/4/5), so
``poseidon.core.runlog.RunLogWriter`` mints ``uuid.uuid4()`` ids in Python
and inserts them explicitly -- a documented v1 substitution (see that
module's docstring) that Phase 10 (History + RLS, doc 08) revisits once
durable ``conversations``/``messages`` land and id time-ordering starts to
matter for insert locality. Nothing in Phase 6 depends on these ids being
time-ordered.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TURN_RUN = "turn_run"
_LLM_CALLS = "llm_calls"
_TOOL_CALLS = "tool_calls"

# doc 06 section 1's three CHECK constraints, transcribed verbatim. The two
# call tables share one status vocabulary ('running'/'clarify' are turn-level
# outcomes with no per-call equivalent), so one constant serves both.
_TURN_RUN_KIND_CHECK = "kind in ('chat_turn', 'memory_update')"
_TURN_RUN_STATUS_CHECK = "status in ('running', 'ok', 'clarify', 'error')"
_CALL_STATUS_CHECK = "status in ('ok', 'error')"

_TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)


def _turn_run_columns() -> list[sa.Column]:
    """The parent row: one per chat turn (``kind='chat_turn'``) or background
    LLM run (``kind='memory_update'``) -- doc 06 section 1's ``turn_run``.
    Returned as a fresh list on every call (matching 0002's ``_columns()``
    pattern): a ``sa.Column`` is single-use once bound to a ``Table``, and a
    plain module-level list of live columns would break the moment anything
    ever needed a second ``turn_run`` table built from the same definition
    (a future test fixture, a second call in the same process).
    """
    return [
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="chat_turn"),
        sa.Column("conversation_id", postgresql.UUID(), nullable=True),
        sa.Column("message_id", postgresql.UUID(), nullable=True),
        sa.Column("user_sub", sa.Text(), nullable=False),
        sa.Column("client_turn_key", postgresql.UUID(), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column(
            "parsed",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("answer_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", _TIMESTAMPTZ, nullable=True),
    ]


def _llm_calls_columns() -> list[sa.Column]:
    """One append-only row per model call within a turn -- doc 06 section 1's
    ``llm_calls``, decision D27 (per-call, never summed into the parent until
    ``RunLogWriter.finalize`` rolls the totals up)."""
    return [
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "turn_run_id",
            postgresql.UUID(),
            sa.ForeignKey(f"{_TURN_RUN}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_sub", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
    ]


def _tool_calls_columns() -> list[sa.Column]:
    """One append-only row per tool dispatch within a turn -- doc 06 section
    1's ``tool_calls``. ``args`` is verbatim; ``result_digest`` is a digest
    (row counts, checksums, artifact refs), never the rows themselves (doc 06
    section 2)."""
    return [
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "turn_run_id",
            postgresql.UUID(),
            sa.ForeignKey(f"{_TURN_RUN}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_sub", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("server", sa.Text(), nullable=True),
        sa.Column("args", postgresql.JSONB(), nullable=False),
        sa.Column("result_digest", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.create_table(
        _TURN_RUN,
        *_turn_run_columns(),
        sa.CheckConstraint(_TURN_RUN_KIND_CHECK, name="ck_turn_run_kind"),
        sa.CheckConstraint(_TURN_RUN_STATUS_CHECK, name="ck_turn_run_status"),
        sa.UniqueConstraint(
            "user_sub", "client_turn_key", name="uq_turn_run_user_sub_client_turn_key"
        ),
    )
    op.create_index(
        "ix_turn_run_conversation_id_turn_index",
        _TURN_RUN,
        ["conversation_id", "turn_index"],
    )
    op.create_index("ix_turn_run_created_at", _TURN_RUN, ["created_at"])

    op.create_table(
        _LLM_CALLS,
        *_llm_calls_columns(),
        sa.CheckConstraint(_CALL_STATUS_CHECK, name="ck_llm_calls_status"),
        sa.UniqueConstraint("turn_run_id", "seq", name="uq_llm_calls_turn_run_id_seq"),
    )
    op.create_index("ix_llm_calls_turn_run_id_seq", _LLM_CALLS, ["turn_run_id", "seq"])

    op.create_table(
        _TOOL_CALLS,
        *_tool_calls_columns(),
        sa.CheckConstraint(_CALL_STATUS_CHECK, name="ck_tool_calls_status"),
        sa.UniqueConstraint("turn_run_id", "seq", name="uq_tool_calls_turn_run_id_seq"),
    )
    op.create_index("ix_tool_calls_turn_run_id_seq", _TOOL_CALLS, ["turn_run_id", "seq"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_table(_TOOL_CALLS)
    op.drop_table(_LLM_CALLS)
    op.drop_table(_TURN_RUN)

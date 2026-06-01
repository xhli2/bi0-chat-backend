"""Session persistence tables and chat_messages extensions.

Revision ID: 20260531_0002
Revises: 20260520_0001
Create Date: 2026-05-31 12:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260531_0002"
down_revision = "20260520_0001"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(name)


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if _table_exists("chat_messages"):
        if not _column_exists("chat_messages", "task_id"):
            op.add_column("chat_messages", sa.Column("task_id", sa.String(length=36), nullable=True))
            op.create_index("ix_chat_messages_task_id", "chat_messages", ["task_id"], unique=False)
        if not _column_exists("chat_messages", "metadata"):
            op.add_column(
                "chat_messages",
                sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            )

    if not _table_exists("chat_tool_calls"):
        op.create_table(
            "chat_tool_calls",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=False),
            sa.Column("turn_index", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.String(length=36), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("tool_name", sa.String(length=120), nullable=False),
            sa.Column("call_id", sa.String(length=36), nullable=False),
            sa.Column("input_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("output_json", sa.JSON(), nullable=True),
            sa.Column("output_ref", sa.String(length=512), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_chat_tool_calls_session_id", "chat_tool_calls", ["session_id"], unique=False)
        op.create_index("ix_chat_tool_calls_turn_index", "chat_tool_calls", ["turn_index"], unique=False)
        op.create_index("ix_chat_tool_calls_task_id", "chat_tool_calls", ["task_id"], unique=False)
        op.create_index("idx_tool_calls_session_turn", "chat_tool_calls", ["session_id", "turn_index"], unique=False)

    if not _table_exists("session_entities"):
        op.create_table(
            "session_entities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=False),
            sa.Column("entity_type", sa.String(length=32), nullable=False),
            sa.Column("canonical_id", sa.String(length=255), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=True),
            sa.Column("genome_build", sa.String(length=16), nullable=True),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("source_turn", sa.Integer(), nullable=True),
            sa.Column("source_tool_call_id", sa.String(length=36), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("raw_ref", sa.String(length=512), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("session_id", "entity_type", "canonical_id", name="uq_session_entity"),
        )
        op.create_index("ix_session_entities_session_id", "session_entities", ["session_id"], unique=False)
        op.create_index("idx_session_entities_session", "session_entities", ["session_id", "is_active"], unique=False)

    if not _table_exists("session_runs"):
        op.create_table(
            "session_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=False),
            sa.Column("turn_index", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="public"),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("agent_type", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=64), nullable=True),
            sa.Column("context_policy", sa.String(length=32), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
            sa.Column("plan_json", sa.JSON(), nullable=True),
            sa.Column("usage_json", sa.JSON(), nullable=True),
            sa.Column("routing_json", sa.JSON(), nullable=True),
            sa.Column("resolved_skills", sa.JSON(), nullable=True),
            sa.Column("context_pack_ids", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_session_runs_session_id", "session_runs", ["session_id"], unique=False)
        op.create_index("idx_session_runs_session", "session_runs", ["session_id", "created_at"], unique=False)

    if not _table_exists("session_artifacts"):
        op.create_table(
            "session_artifacts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=False),
            sa.Column("turn_index", sa.Integer(), nullable=True),
            sa.Column("task_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("storage_path", sa.String(length=512), nullable=False),
            sa.Column("mime_type", sa.String(length=64), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_session_artifacts_session_id", "session_artifacts", ["session_id"], unique=False)
        op.create_index("idx_session_artifacts_session", "session_artifacts", ["session_id", "kind"], unique=False)

    if _table_exists("spliceai_jobs"):
        if not _column_exists("spliceai_jobs", "turn_index"):
            op.add_column("spliceai_jobs", sa.Column("turn_index", sa.Integer(), nullable=True))
            op.create_index("ix_spliceai_jobs_turn_index", "spliceai_jobs", ["turn_index"], unique=False)
        if not _column_exists("spliceai_jobs", "message_id"):
            op.add_column("spliceai_jobs", sa.Column("message_id", sa.String(length=36), nullable=True))
            op.create_index("ix_spliceai_jobs_message_id", "spliceai_jobs", ["message_id"], unique=False)
        if not _column_exists("spliceai_jobs", "tool_call_id"):
            op.add_column("spliceai_jobs", sa.Column("tool_call_id", sa.String(length=36), nullable=True))
            op.create_index("ix_spliceai_jobs_tool_call_id", "spliceai_jobs", ["tool_call_id"], unique=False)


def downgrade() -> None:
    if _table_exists("spliceai_jobs"):
        for col in ("tool_call_id", "message_id", "turn_index"):
            if _column_exists("spliceai_jobs", col):
                op.drop_column("spliceai_jobs", col)

    for table in ("session_artifacts", "session_runs", "session_entities", "chat_tool_calls"):
        if _table_exists(table):
            op.drop_table(table)

    if _table_exists("chat_messages"):
        for col in ("metadata", "task_id"):
            if _column_exists("chat_messages", col):
                op.drop_column("chat_messages", col)

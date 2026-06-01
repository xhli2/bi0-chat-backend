"""Initial schema with Python Alembic migration.

Revision ID: 20260520_0001
Revises:
Create Date: 2026-05-20 23:55:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260520_0001"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(name)


def upgrade() -> None:
    if not _table_exists("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("hashed_password", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if not _table_exists("items"):
        op.create_table(
            "items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_items_title", "items", ["title"], unique=False)
        op.create_index("ix_items_owner_id", "items", ["owner_id"], unique=False)

    if not _table_exists("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="public"),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False, server_default="New Session"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"], unique=False)
        op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"], unique=False)
        op.create_index("ix_chat_sessions_status", "chat_sessions", ["status"], unique=False)

    if not _table_exists("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=False),
            sa.Column("turn_index", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"], unique=False)
        op.create_index("ix_chat_messages_turn_index", "chat_messages", ["turn_index"], unique=False)
        op.create_index("ix_chat_messages_role", "chat_messages", ["role"], unique=False)
        op.create_index("ix_chat_messages_trace_id", "chat_messages", ["trace_id"], unique=False)
        op.create_index("ix_chat_messages_is_archived", "chat_messages", ["is_archived"], unique=False)

    if not _table_exists("chat_summaries"):
        op.create_table(
            "chat_summaries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=False),
            sa.Column("summary_text", sa.Text(), nullable=False),
            sa.Column("summary_short", sa.Text(), nullable=True),
            sa.Column("covered_until_turn", sa.Integer(), nullable=False),
            sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_chat_summaries_session_id", "chat_summaries", ["session_id"], unique=False)
        op.create_index("ix_chat_summaries_covered_until_turn", "chat_summaries", ["covered_until_turn"], unique=False)
        op.create_index("ix_chat_summaries_version", "chat_summaries", ["version"], unique=False)
        op.create_index("ix_chat_summaries_is_archived", "chat_summaries", ["is_archived"], unique=False)
        op.create_index("ix_chat_summaries_trace_id", "chat_summaries", ["trace_id"], unique=False)

    if not _table_exists("chat_memory_kv"):
        op.create_table(
            "chat_memory_kv",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=False),
            sa.Column("key", sa.String(length=120), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("importance", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("source_turn", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_chat_memory_kv_session_id", "chat_memory_kv", ["session_id"], unique=False)
        op.create_index("ix_chat_memory_kv_key", "chat_memory_kv", ["key"], unique=False)
        op.create_index("ix_chat_memory_kv_importance", "chat_memory_kv", ["importance"], unique=False)
        op.create_index("ix_chat_memory_kv_expires_at", "chat_memory_kv", ["expires_at"], unique=False)

    if not _table_exists("approval_tickets"):
        op.create_table(
            "approval_tickets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("task_id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=36), nullable=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="public"),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_approval_tickets_task_id", "approval_tickets", ["task_id"], unique=False)
        op.create_index("ix_approval_tickets_session_id", "approval_tickets", ["session_id"], unique=False)
        op.create_index("ix_approval_tickets_tenant_id", "approval_tickets", ["tenant_id"], unique=False)
        op.create_index("ix_approval_tickets_tool_name", "approval_tickets", ["tool_name"], unique=False)
        op.create_index("ix_approval_tickets_status", "approval_tickets", ["status"], unique=False)
        op.create_index("ix_approval_tickets_requested_by", "approval_tickets", ["requested_by"], unique=False)
        op.create_index("ix_approval_tickets_reviewer_id", "approval_tickets", ["reviewer_id"], unique=False)
        op.create_index("ix_approval_tickets_trace_id", "approval_tickets", ["trace_id"], unique=False)

    if not _table_exists("spliceai_jobs"):
        op.create_table(
            "spliceai_jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("chat_sessions.id"), nullable=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="public"),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("variant_hgvs", sa.String(length=255), nullable=False),
            sa.Column("genome_build", sa.String(length=16), nullable=False, server_default="GRCh38"),
            sa.Column("gene_symbol", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=64), nullable=False, server_default="spliceai-mock-v1"),
            sa.Column("input_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("archived_result", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_spliceai_jobs_session_id", "spliceai_jobs", ["session_id"], unique=False)
        op.create_index("ix_spliceai_jobs_tenant_id", "spliceai_jobs", ["tenant_id"], unique=False)
        op.create_index("ix_spliceai_jobs_user_id", "spliceai_jobs", ["user_id"], unique=False)
        op.create_index("ix_spliceai_jobs_trace_id", "spliceai_jobs", ["trace_id"], unique=False)
        op.create_index("ix_spliceai_jobs_status", "spliceai_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS spliceai_jobs")
    op.execute("DROP TABLE IF EXISTS approval_tickets")
    op.execute("DROP TABLE IF EXISTS chat_memory_kv")
    op.execute("DROP TABLE IF EXISTS chat_summaries")
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute("DROP TABLE IF EXISTS chat_sessions")
    op.execute("DROP TABLE IF EXISTS items")
    op.execute("DROP TABLE IF EXISTS users")

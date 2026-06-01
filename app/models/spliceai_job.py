from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SpliceAIJob(Base, TimestampMixin):
    __tablename__ = "spliceai_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("chat_sessions.id"), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="public")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    variant_hgvs: Mapped[str] = mapped_column(String(255))
    genome_build: Mapped[str] = mapped_column(String(16), default="GRCh38")
    gene_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), default="spliceai-mock-v1")
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    archived_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    turn_index: Mapped[int | None] = mapped_column(nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

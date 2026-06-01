from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class SessionRun(Base, TimestampMixin):
    __tablename__ = "session_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    turn_index: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="public")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_type: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_policy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="running")
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    routing_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resolved_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    context_pack_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session = relationship("ChatSession", back_populates="runs")

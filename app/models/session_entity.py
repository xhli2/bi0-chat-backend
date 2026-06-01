from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class SessionEntity(Base, TimestampMixin):
    __tablename__ = "session_entities"
    __table_args__ = (UniqueConstraint("session_id", "entity_type", "canonical_id", name="uq_session_entity"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    canonical_id: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    genome_build: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_tool_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    session = relationship("ChatSession", back_populates="entities")

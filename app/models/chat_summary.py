from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ChatSummary(Base, TimestampMixin):
    __tablename__ = "chat_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    summary_text: Mapped[str] = mapped_column(Text)
    summary_short: Mapped[str | None] = mapped_column(Text, nullable=True)
    covered_until_turn: Mapped[int] = mapped_column(Integer, index=True)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, index=True, default=1)
    is_archived: Mapped[bool] = mapped_column(default=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    session = relationship("ChatSession", back_populates="summaries")

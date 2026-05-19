from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.db.session import SessionLocal
from app.services.session_history import SessionHistoryService


class TimeNowInput(BaseModel):
    timezone_name: str = Field(default="UTC")


class TimeNowOutput(BaseModel):
    now_iso: str
    timezone_name: str


async def tool_time_now(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = args
    now = datetime.now(timezone.utc)
    return TimeNowOutput(now_iso=now.isoformat(), timezone_name="UTC").model_dump(mode="python")


class SessionLookupInput(BaseModel):
    session_id: str
    message_limit: int = Field(default=8, ge=1, le=50)


class SessionLookupOutput(BaseModel):
    session_id: str
    title: str
    latest_summary: str | None = None
    recent_messages: list[str]


async def tool_session_lookup(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    payload = SessionLookupInput.model_validate(args)
    runtime_ctx = _.get("context") if isinstance(_, dict) else None
    tenant_id = getattr(runtime_ctx, "tenant_id", None)
    user_id = getattr(runtime_ctx, "user_id", None)
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        session = await history.get_session(payload.session_id, tenant_id=tenant_id, user_id=user_id)
        if session is None:
            return SessionLookupOutput(
                session_id=payload.session_id,
                title="Not Found",
                latest_summary=None,
                recent_messages=[],
            ).model_dump(mode="python")
        summary = await history.get_latest_summary(payload.session_id)
        recent = await history.get_recent_messages(payload.session_id, limit=payload.message_limit)
        lines = [f"{m.role}: {m.content[:140]}" for m in recent]
        return SessionLookupOutput(
            session_id=session.id,
            title=session.title,
            latest_summary=summary.summary_short if summary else None,
            recent_messages=lines,
        ).model_dump(mode="python")


class SummarizeChunkInput(BaseModel):
    text: str = Field(min_length=1)
    max_chars: int = Field(default=500, ge=100, le=2000)


class SummarizeChunkOutput(BaseModel):
    summary: str


async def tool_summarize_chunk(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    payload = SummarizeChunkInput.model_validate(args)
    text = payload.text.strip()
    compact = " ".join(text.split())
    summary = compact[: payload.max_chars]
    return SummarizeChunkOutput(summary=summary).model_dump(mode="python")

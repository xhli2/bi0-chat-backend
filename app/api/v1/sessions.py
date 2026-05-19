from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.core.exceptions import ApiError
from app.db.session import get_db_session
from app.schemas.session import (
    SessionCreateRequest,
    SessionDiagnosticsOut,
    SessionMemoryOut,
    SessionMessageOut,
    SessionOut,
    SessionSummaryOut,
)
from app.services.session_history import SessionHistoryService
from app.worker.tasks import refresh_session_memory_task

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _to_session_out(session) -> SessionOut:
    return SessionOut(
        id=session.id,
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        title=session.title,
        status=session.status,
        last_active_at=session.last_active_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _to_message_out(message) -> SessionMessageOut:
    return SessionMessageOut(
        id=message.id,
        session_id=message.session_id,
        turn_index=message.turn_index,
        role=message.role,
        content=message.content,
        token_estimate=message.token_estimate,
        trace_id=message.trace_id,
        is_archived=message.is_archived,
        created_at=message.created_at,
    )


def _to_memory_out(item) -> SessionMemoryOut:
    return SessionMemoryOut(
        id=item.id,
        session_id=item.session_id,
        key=item.key,
        value=item.value,
        importance=item.importance,
        source_turn=item.source_turn,
        expires_at=item.expires_at,
        created_at=item.created_at,
    )


def _to_summary_out(summary) -> SessionSummaryOut:
    return SessionSummaryOut(
        id=summary.id,
        session_id=summary.session_id,
        summary_text=summary.summary_text,
        summary_short=summary.summary_short,
        covered_until_turn=summary.covered_until_turn,
        token_estimate=summary.token_estimate,
        version=summary.version,
        is_archived=summary.is_archived,
        created_at=summary.created_at,
    )


@router.post("", response_model=SessionOut)
async def create_session(
    payload: SessionCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SessionOut:
    service = SessionHistoryService(db)
    session = await service.create_session(tenant_id=auth.tenant_id, user_id=auth.user.id, title=payload.title)
    return _to_session_out(session)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SessionOut:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    return _to_session_out(session)


@router.get("/{session_id}/messages", response_model=list[SessionMessageOut])
async def get_session_messages(
    session_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    include_archived: bool = Query(False),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[SessionMessageOut]:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    messages = await service.list_messages(session_id, page=page, size=size, include_archived=include_archived)
    return [_to_message_out(msg) for msg in messages]


@router.get("/{session_id}/memory", response_model=list[SessionMemoryOut])
async def get_session_memory(
    session_id: str,
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[SessionMemoryOut]:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    items = await service.get_memory(session_id=session_id, limit=limit)
    return [_to_memory_out(item) for item in items]


@router.get("/{session_id}/summary", response_model=SessionSummaryOut | None)
async def get_latest_summary(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SessionSummaryOut | None:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    summary = await service.get_latest_summary(session_id)
    if summary is None:
        return None
    return _to_summary_out(summary)


@router.post("/{session_id}/summarize")
async def summarize_session(
    session_id: str,
    trace_id: str | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    refresh_session_memory_task.apply_async(kwargs={"session_id": session_id, "trace_id": trace_id})
    return {"message": "summary_job_enqueued", "session_id": session_id}


@router.get("/{session_id}/diagnostics", response_model=SessionDiagnosticsOut)
async def session_diagnostics(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SessionDiagnosticsOut:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    diag = await service.diagnostics(session_id)
    return SessionDiagnosticsOut(session_id=session_id, **diag)

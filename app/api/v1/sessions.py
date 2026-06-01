from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.core.exceptions import ApiError
from app.db.session import get_db_session
from app.schemas.session import (
    SessionArtifactOut,
    SessionCreateRequest,
    SessionDiagnosticsOut,
    SessionEntityOut,
    SessionListOut,
    SessionMemoryOut,
    SessionMessageOut,
    SessionOut,
    SessionRunOut,
    SessionSummaryOut,
    SessionTimelineOut,
    SessionTokenUsageOut,
    SessionToolCallOut,
    UserTokenUsageOut,
)
from app.services.session_history import SessionHistoryService
from app.services.session_persistence import SessionPersistenceService
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
        task_id=message.task_id,
        metadata=message.metadata_json or {},
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


def _to_run_out(run) -> SessionRunOut:
    return SessionRunOut(
        id=run.id,
        session_id=run.session_id,
        turn_index=run.turn_index,
        trace_id=run.trace_id,
        agent_type=run.agent_type,
        model=run.model,
        context_policy=run.context_policy,
        status=run.status,
        usage=run.usage_json,
        resolved_skills=list(run.resolved_skills or []),
        context_pack_ids=list(run.context_pack_ids or []),
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def _to_tool_call_out(item) -> SessionToolCallOut:
    return SessionToolCallOut(
        id=item.id,
        session_id=item.session_id,
        turn_index=item.turn_index,
        task_id=item.task_id,
        tool_name=item.tool_name,
        call_id=item.call_id,
        status=item.status,
        input_json=item.input_json or {},
        output_json=item.output_json,
        output_ref=item.output_ref,
        duration_ms=item.duration_ms,
        created_at=item.created_at,
    )


def _to_entity_out(item) -> SessionEntityOut:
    return SessionEntityOut(
        id=item.id,
        session_id=item.session_id,
        entity_type=item.entity_type,
        canonical_id=item.canonical_id,
        display_name=item.display_name,
        genome_build=item.genome_build,
        source=item.source,
        source_turn=item.source_turn,
        summary=item.summary,
        raw_ref=item.raw_ref,
        is_active=item.is_active,
        created_at=item.created_at,
    )


def _to_artifact_out(item) -> SessionArtifactOut:
    return SessionArtifactOut(
        id=item.id,
        session_id=item.session_id,
        turn_index=item.turn_index,
        task_id=item.task_id,
        run_id=item.run_id,
        kind=item.kind,
        filename=item.filename,
        storage_path=item.storage_path,
        mime_type=item.mime_type,
        sha256=item.sha256,
        size_bytes=item.size_bytes,
        metadata=item.metadata_json or {},
        created_at=item.created_at,
    )


@router.get("", response_model=SessionListOut)
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SessionListOut:
    service = SessionHistoryService(db)
    sessions = await service.list_sessions(tenant_id=auth.tenant_id, user_id=auth.user.id, limit=limit)
    return SessionListOut(items=[_to_session_out(item) for item in sessions], total=len(sessions))


@router.get("/token-usage", response_model=UserTokenUsageOut)
async def get_user_token_usage(
    session_limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> UserTokenUsageOut:
    service = SessionHistoryService(db)
    total, by_session = await service.get_user_token_usage(
        tenant_id=auth.tenant_id,
        user_id=auth.user.id,
        session_limit=session_limit,
    )
    return UserTokenUsageOut(
        tenant_id=auth.tenant_id,
        user_id=auth.user.id,
        session_count=len(by_session),
        usage=total,
        by_session=[SessionTokenUsageOut(session_id=sid, usage=usage) for sid, usage in by_session],
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


@router.get("/{session_id}/token-usage", response_model=SessionTokenUsageOut)
async def get_session_token_usage(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SessionTokenUsageOut:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    usage = await service.get_session_token_usage(session_id)
    return SessionTokenUsageOut(session_id=session_id, usage=usage)


@router.get("/{session_id}/runs", response_model=list[SessionRunOut])
async def get_session_runs(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[SessionRunOut]:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    runs = await service.list_session_runs(session_id, limit=limit)
    return [_to_run_out(run) for run in runs]


@router.get("/{session_id}/timeline", response_model=SessionTimelineOut)
async def get_session_timeline(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SessionTimelineOut:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    messages = await service.list_messages(session_id, page=1, size=200)
    runs = await service.list_session_runs(session_id, limit=100)
    tool_calls = await service.list_tool_calls(session_id, limit=200)
    entities = await service.list_entities(session_id)
    persistence = SessionPersistenceService(db)
    artifacts = await persistence.list_artifacts(session_id, limit=100)
    token_usage = await service.get_session_token_usage(session_id)
    return SessionTimelineOut(
        session_id=session_id,
        messages=[_to_message_out(msg) for msg in messages],
        runs=[_to_run_out(run) for run in runs],
        tool_calls=[_to_tool_call_out(item) for item in tool_calls],
        entities=[_to_entity_out(item) for item in entities],
        artifacts=[_to_artifact_out(item) for item in artifacts],
        token_usage=token_usage,
    )


@router.get("/{session_id}/artifacts", response_model=list[SessionArtifactOut])
async def get_session_artifacts(
    session_id: str,
    limit: int = Query(100, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[SessionArtifactOut]:
    service = SessionHistoryService(db)
    session = await service.get_session(session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    persistence = SessionPersistenceService(db)
    artifacts = await persistence.list_artifacts(session_id, limit=limit)
    return [_to_artifact_out(item) for item in artifacts]


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

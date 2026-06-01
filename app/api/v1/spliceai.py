from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.core.config import get_settings
from app.core.exceptions import ApiError
from app.db.session import get_db_session
from app.schemas.spliceai import SpliceAIJobOut, SpliceAIResult, SpliceAISubmitRequest, SpliceAISubmitResponse
from app.services.session_history import SessionHistoryService
from app.services.spliceai_jobs import SpliceAIJobService
from app.worker.tasks import run_spliceai_job_inline, run_spliceai_job_task

router = APIRouter(prefix="/spliceai", tags=["spliceai"])
settings = get_settings()


def _to_job_out(job) -> SpliceAIJobOut:
    result = SpliceAIResult.model_validate(job.archived_result) if job.archived_result else None
    return SpliceAIJobOut(
        id=job.id,
        session_id=job.session_id,
        tenant_id=job.tenant_id,
        user_id=job.user_id,
        trace_id=job.trace_id,
        status=job.status,
        variant_hgvs=job.variant_hgvs,
        genome_build=job.genome_build,
        gene_symbol=job.gene_symbol,
        model_version=job.model_version,
        input_payload=job.input_payload,
        archived_result=result,
        error_message=job.error_message,
        queued_at=job.queued_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/jobs", response_model=SpliceAISubmitResponse)
async def submit_spliceai_job(
    payload: SpliceAISubmitRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SpliceAISubmitResponse:
    if payload.session_id:
        history = SessionHistoryService(db)
        session = await history.get_session(payload.session_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
        if session is None:
            raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")

    service = SpliceAIJobService(db)
    job = await service.create_job(
        tenant_id=auth.tenant_id,
        user_id=auth.user.id,
        session_id=payload.session_id,
        trace_id=auth.trace_id,
        variant_hgvs=payload.variant_hgvs,
        genome_build=payload.genome_build,
        gene_symbol=payload.gene_symbol,
        input_payload=payload.model_dump(mode="python"),
    )
    if settings.environment == "test":
        await run_spliceai_job_inline(job_id=job.id, trace_id=auth.trace_id, tenant_id=auth.tenant_id)
    else:
        run_spliceai_job_task.apply_async(
            kwargs={"job_id": job.id, "trace_id": auth.trace_id, "tenant_id": auth.tenant_id},
            queue="default",
            routing_key="default",
        )
    return SpliceAISubmitResponse(job_id=job.id, status=job.status, status_url=f"/api/v1/spliceai/jobs/{job.id}")


@router.get("/jobs/{job_id}", response_model=SpliceAIJobOut)
async def get_spliceai_job(
    job_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> SpliceAIJobOut:
    service = SpliceAIJobService(db)
    job = await service.get_job(job_id, tenant_id=auth.tenant_id, user_id=auth.user.id)
    if job is None:
        raise ApiError(status_code=404, code="SPLICEAI_JOB_NOT_FOUND", detail="SpliceAI job not found.")
    return _to_job_out(job)


@router.get("/jobs", response_model=list[SpliceAIJobOut])
async def list_spliceai_jobs(
    limit: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[SpliceAIJobOut]:
    service = SpliceAIJobService(db)
    jobs = await service.list_jobs(tenant_id=auth.tenant_id, user_id=auth.user.id, limit=limit)
    return [_to_job_out(job) for job in jobs]

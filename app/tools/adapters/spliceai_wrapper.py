from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.db.session import SessionLocal
from app.schemas.spliceai import SpliceAIResult
from app.services.spliceai_jobs import SpliceAIJobService
from app.services.bio_evidence import attach_evidence, evidence_from_spliceai_job, evidence_from_spliceai_result
from app.tools.schemas import ToolExecutionError


class SpliceAISubmitInput(BaseModel):
    variant_hgvs: str = Field(min_length=3, max_length=255)
    genome_build: str = Field(default="GRCh38", pattern="^(GRCh37|GRCh38)$")
    gene_symbol: str | None = Field(default=None, max_length=64)
    session_id: str | None = None


class SpliceAISubmitOutput(BaseModel):
    job_id: str
    status: str
    queued: bool
    evidence: dict | None = None
    evidence_pack: dict | None = None


class SpliceAIGetResultInput(BaseModel):
    job_id: str = Field(min_length=1)


class SpliceAIGetResultOutput(BaseModel):
    job_id: str
    status: str
    result: SpliceAIResult | None = None
    error_message: str | None = None


async def _enqueue_spliceai_job(*, job_id: str, trace_id: str | None, tenant_id: str) -> None:
    from app.worker.celery_app import celery_app

    celery_app.send_task(
        "spliceai.run",
        kwargs={"job_id": job_id, "trace_id": trace_id, "tenant_id": tenant_id},
    )


async def tool_spliceai_submit(args: dict, runtime: dict) -> dict:
    payload = SpliceAISubmitInput.model_validate(args)
    context = runtime.get("context") if isinstance(runtime, dict) else None
    if context is None:
        raise ToolExecutionError("MISSING_CONTEXT", "Tool runtime context is required.")

    async with SessionLocal() as db:
        service = SpliceAIJobService(db)
        job = await service.create_job(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=payload.session_id or context.session_id,
            trace_id=context.trace_id,
            variant_hgvs=payload.variant_hgvs,
            genome_build=payload.genome_build,
            gene_symbol=payload.gene_symbol,
            input_payload=payload.model_dump(mode="python"),
        )
    await _enqueue_spliceai_job(job_id=job.id, trace_id=context.trace_id, tenant_id=context.tenant_id)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    output = SpliceAISubmitOutput(job_id=job.id, status=job.status, queued=True).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_spliceai_job(
            job_id=job.id,
            variant_hgvs=payload.variant_hgvs,
            genome_build=payload.genome_build,
            retrieved_at=retrieved_at,
        ),
    )


async def tool_spliceai_get_result(args: dict, runtime: dict) -> dict:
    payload = SpliceAIGetResultInput.model_validate(args)
    context = runtime.get("context") if isinstance(runtime, dict) else None
    if context is None:
        raise ToolExecutionError("MISSING_CONTEXT", "Tool runtime context is required.")

    async with SessionLocal() as db:
        service = SpliceAIJobService(db)
        job = await service.get_job(payload.job_id, tenant_id=context.tenant_id, user_id=context.user_id)
        if job is None:
            raise ToolExecutionError("SPLICEAI_JOB_NOT_FOUND", "SpliceAI job not found.")
        result = SpliceAIResult.model_validate(job.archived_result) if job.archived_result else None
        output = SpliceAIGetResultOutput(
            job_id=job.id,
            status=job.status,
            result=result,
            error_message=job.error_message,
        ).model_dump(mode="python")
        if job.status == "success" and result is not None:
            output = attach_evidence(
                output,
                evidence_from_spliceai_result(
                    job_id=job.id,
                    variant_hgvs=job.variant_hgvs,
                    genome_build=job.genome_build,
                    result=result,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
        return output

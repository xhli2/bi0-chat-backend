from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spliceai_job import SpliceAIJob
from app.schemas.spliceai import SpliceAIResult


class SpliceAIJobService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_job(
        self,
        *,
        tenant_id: str,
        user_id: int | None,
        session_id: str | None,
        trace_id: str | None,
        variant_hgvs: str,
        genome_build: str,
        gene_symbol: str | None,
        input_payload: dict,
    ) -> SpliceAIJob:
        job = SpliceAIJob(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            status="queued",
            variant_hgvs=variant_hgvs,
            genome_build=genome_build,
            gene_symbol=gene_symbol,
            input_payload=input_payload,
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def get_job(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
        user_id: int | None = None,
    ) -> SpliceAIJob | None:
        result = await self.db.execute(select(SpliceAIJob).where(SpliceAIJob.id == job_id))
        job = result.scalars().first()
        if job is None:
            return None
        if tenant_id is not None and job.tenant_id != tenant_id:
            return None
        if user_id is not None and job.user_id != user_id:
            return None
        return job

    async def list_jobs(self, *, tenant_id: str, user_id: int | None, limit: int = 20) -> list[SpliceAIJob]:
        stmt: Select[tuple[SpliceAIJob]] = (
            select(SpliceAIJob)
            .where(SpliceAIJob.tenant_id == tenant_id)
            .order_by(SpliceAIJob.created_at.desc())
            .limit(limit)
        )
        if user_id is not None:
            stmt = stmt.where(SpliceAIJob.user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def mark_running(self, job_id: str) -> SpliceAIJob | None:
        job = await self.get_job(job_id)
        if job is None:
            return None
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.error_message = None
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def mark_success(self, job_id: str, result_payload: SpliceAIResult) -> SpliceAIJob | None:
        job = await self.get_job(job_id)
        if job is None:
            return None
        job.status = "success"
        job.archived_result = result_payload.model_dump(mode="python")
        job.error_message = None
        job.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def mark_failed(self, job_id: str, error_message: str) -> SpliceAIJob | None:
        job = await self.get_job(job_id)
        if job is None:
            return None
        job.status = "failed"
        job.error_message = error_message[:4000]
        job.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(job)
        return job

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SpliceAIJobStatus = Literal["queued", "running", "success", "failed"]


class SpliceAISubmitRequest(BaseModel):
    variant_hgvs: str = Field(min_length=3, max_length=255)
    genome_build: str = Field(default="GRCh38", pattern="^(GRCh37|GRCh38)$")
    gene_symbol: str | None = Field(default=None, max_length=64)
    session_id: str | None = None


class SpliceAIScoreBreakdown(BaseModel):
    ds_ag: float
    ds_al: float
    ds_dg: float
    ds_dl: float
    max_score: float


class SpliceAIResult(BaseModel):
    variant_hgvs: str
    genome_build: str
    gene_symbol: str | None
    model_version: str
    score_breakdown: SpliceAIScoreBreakdown
    predicted_impact: str
    interpretation: str
    source: str
    computed_at: str


class SpliceAIJobOut(BaseModel):
    id: str
    session_id: str | None
    tenant_id: str
    user_id: int | None
    trace_id: str | None
    status: SpliceAIJobStatus
    variant_hgvs: str
    genome_build: str
    gene_symbol: str | None
    model_version: str
    input_payload: dict
    archived_result: SpliceAIResult | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SpliceAISubmitResponse(BaseModel):
    job_id: str
    status: SpliceAIJobStatus
    status_url: str

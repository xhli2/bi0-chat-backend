from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas.spliceai import SpliceAIResult


async def score_variant_via_service(
    *,
    variant_hgvs: str,
    genome_build: str,
    gene_symbol: str | None,
) -> SpliceAIResult:
    settings = get_settings()
    if not settings.spliceai_service_url:
        raise RuntimeError("SpliceAI service URL is not configured.")

    payload: dict[str, Any] = {
        "variant_hgvs": variant_hgvs,
        "genome_build": genome_build,
        "gene_symbol": gene_symbol,
    }
    url = settings.spliceai_service_url.rstrip("/") + "/score"
    timeout = max(5, settings.spliceai_service_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        body = response.json()
    return SpliceAIResult.model_validate(body)

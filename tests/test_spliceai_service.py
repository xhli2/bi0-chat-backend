import pytest

from app.services.spliceai_client import score_variant_via_service
from app.worker import tasks as worker_tasks


@pytest.mark.asyncio
async def test_spliceai_uses_mock_in_test_env(monkeypatch):
    called = {"remote": False}

    async def _fake_remote(**kwargs):
        called["remote"] = True
        raise AssertionError("remote service should not be called in test")

    monkeypatch.setattr(worker_tasks, "score_variant_via_service", _fake_remote)
    result = worker_tasks._simulate_spliceai_result(
        variant_hgvs="NM_007294.3:c.5266dupC",
        genome_build="GRCh38",
        gene_symbol="BRCA1",
        trace_id="trace-1",
    )
    assert result.source == "spliceai-simulated-worker"
    assert called["remote"] is False


@pytest.mark.asyncio
async def test_spliceai_client_requires_service_url(monkeypatch):
    monkeypatch.setenv("SPLICEAI_SERVICE_URL", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        await score_variant_via_service(
            variant_hgvs="NM_007294.3:c.5266dupC",
            genome_build="GRCh38",
            gene_symbol="BRCA1",
        )
    get_settings.cache_clear()

import pytest

from app.tools.adapters.spliceai_wrapper import tool_spliceai_get_result, tool_spliceai_submit
from app.worker.tasks import run_spliceai_job_inline


@pytest.mark.asyncio
async def test_spliceai_tool_submit_and_fetch(monkeypatch):
    captured = {"task_name": None}

    async def _fake_send_task(*, job_id: str, trace_id: str | None, tenant_id: str):
        captured["task_name"] = "spliceai.run"
        captured["kwargs"] = {"job_id": job_id, "trace_id": trace_id, "tenant_id": tenant_id}

    monkeypatch.setattr("app.tools.adapters.spliceai_wrapper._enqueue_spliceai_job", _fake_send_task)
    runtime = {
        "context": type(
            "Ctx",
            (),
            {"tenant_id": "public", "user_id": 1, "session_id": None, "trace_id": "trace-spliceai"},
        )()
    }
    submit_result = await tool_spliceai_submit(
        {"variant_hgvs": "NM_000059.4:c.7790G>A", "genome_build": "GRCh38", "gene_symbol": "BRCA2"},
        runtime,
    )
    assert captured["task_name"] == "spliceai.run"
    job_id = submit_result["job_id"]

    await run_spliceai_job_inline(job_id=job_id, trace_id="trace-spliceai", tenant_id="public")
    get_result = await tool_spliceai_get_result({"job_id": job_id}, runtime)
    assert get_result["status"] == "success"
    assert get_result["result"] is not None

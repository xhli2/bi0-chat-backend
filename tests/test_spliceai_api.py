import pytest


@pytest.mark.asyncio
async def test_spliceai_submit_and_archive_result(client, auth_headers):
    submit = await client.post(
        "/api/v1/spliceai/jobs",
        json={"variant_hgvs": "NM_007294.4:c.68_69del", "genome_build": "GRCh38", "gene_symbol": "BRCA1"},
        headers=auth_headers,
    )
    assert submit.status_code == 200
    body = submit.json()
    job_id = body["job_id"]
    assert body["status"] == "queued"
    assert body["status_url"].endswith(job_id)

    get_job = await client.get(f"/api/v1/spliceai/jobs/{job_id}", headers=auth_headers)
    assert get_job.status_code == 200
    job = get_job.json()
    assert job["status"] == "success"
    assert job["archived_result"] is not None
    assert "score_breakdown" in job["archived_result"]

    list_jobs = await client.get("/api/v1/spliceai/jobs?limit=10", headers=auth_headers)
    assert list_jobs.status_code == 200
    assert any(item["id"] == job_id for item in list_jobs.json())

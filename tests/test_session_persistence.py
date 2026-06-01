import pytest
from pydantic import BaseModel, Field

from app.db.session import SessionLocal
from app.services.bio_evidence import evidence_from_spliceai_job
from app.services.session_history import SessionHistoryService
from app.services.session_persistence import SessionPersistenceService
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.schemas import ToolExecutionContext


class EchoInput(BaseModel):
    text: str = Field(min_length=1)


class EchoOutput(BaseModel):
    echoed: str
    evidence: dict | None = None


async def _echo_with_evidence(args: dict, _: dict) -> dict:
    return {
        "echoed": args["text"],
        "evidence": {
            "source": "spliceai",
            "entity_type": "job",
            "identifiers": {"job_id": "job-123", "variant_hgvs": "NM_000518.5:c.694+1G>A"},
            "genome_build": "GRCh38",
            "retrieved_at": "2026-05-31T00:00:00+00:00",
            "confidence": 1.0,
            "summary": "SpliceAI job queued",
            "raw_ref": "spliceai:job:job-123",
        },
    }


@pytest.mark.asyncio
async def test_record_tool_call_and_entity(monkeypatch):
    monkeypatch.setenv("TENANT_TOOL_POLICIES_JSON", '{"public":["*"]}')
    from app.core.config import get_settings

    get_settings.cache_clear()
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        session = await history.create_session(tenant_id="public", user_id=1, title="Persist test")
        await history.add_message(session_id=session.id, role="user", content="analyze variant", trace_id="t1")

        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="echo_tool",
                description="echo",
                input_schema=EchoInput,
                output_schema=EchoOutput,
                required_permissions=set(),
                timeout_seconds=3,
                executor=_echo_with_evidence,
            )
        )

        executor = ToolExecutor(registry)
        context = ToolExecutionContext(
            tenant_id="public",
            user_id=1,
            session_id=session.id,
            trace_id="t1",
            task_id="task-1",
            permissions=set(),
        )
        result = await executor.execute("echo_tool", {"text": "hello"}, context)
        assert result.ok is True

        persistence = SessionPersistenceService(db)
        tool_calls = await persistence.history.list_tool_calls(session.id)
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "echo_tool"
        assert tool_calls[0].status == "success"
        assert tool_calls[0].output_ref == "spliceai:job:job-123"

        entities = await persistence.history.list_entities(session.id, active_only=True)
        assert len(entities) == 1
        assert entities[0].entity_type == "job"
        assert entities[0].canonical_id == "job-123"


@pytest.mark.asyncio
async def test_prompt_builder_includes_active_analysis():
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        session = await history.create_session(tenant_id="public", user_id=1, title="Prompt test")
        persistence = SessionPersistenceService(db)
        await persistence.upsert_entity_from_evidence(
            session_id=session.id,
            turn_index=1,
            tool_call_id="call-1",
            evidence=evidence_from_spliceai_job(
                job_id="job-abc",
                variant_hgvs="NM_000518.5:c.694+1G>A",
                genome_build="GRCh38",
                retrieved_at="2026-05-31T00:00:00+00:00",
            ),
        )

        from app.services.context_loader import ContextLoader

        loader = ContextLoader(db)
        snapshot = await loader.load_snapshot(
            session_id=session.id,
            tenant_id="public",
            user_prompt="SpliceAI 结果出来了吗",
            context_policy="balanced",
            agent_type="research",
        )
        joined = "\n".join(snapshot.context_blocks)
        assert "[ActiveAnalysis]" in joined
        assert "job-abc" in joined


@pytest.mark.asyncio
async def test_sync_spliceai_job_completion():
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        session = await history.create_session(tenant_id="public", user_id=1, title="SpliceAI persist")
        from app.schemas.spliceai import SpliceAIResult, SpliceAIScoreBreakdown
        from app.services.spliceai_jobs import SpliceAIJobService

        jobs = SpliceAIJobService(db)
        job = await jobs.create_job(
            tenant_id="public",
            user_id=1,
            session_id=session.id,
            trace_id="trace-1",
            variant_hgvs="NM_000518.5:c.694+1G>A",
            genome_build="GRCh38",
            gene_symbol="BRCA1",
            input_payload={},
        )
        persistence = SessionPersistenceService(db)
        result = SpliceAIResult(
            variant_hgvs=job.variant_hgvs,
            genome_build=job.genome_build,
            gene_symbol=job.gene_symbol,
            model_version="spliceai-mock-v1",
            score_breakdown=SpliceAIScoreBreakdown(ds_ag=0.1, ds_al=0.2, ds_dg=0.3, ds_dl=0.9, max_score=0.9),
            predicted_impact="high",
            interpretation="strong impact",
            source="test",
            computed_at="2026-05-31T00:00:00+00:00",
        )
        updated = await jobs.mark_success(job.id, result)
        assert updated is not None
        await persistence.sync_spliceai_job_completion(job=updated, result=result)

        entities = await persistence.history.list_entities(session.id)
        assert any(item.entity_type == "variant" for item in entities)
        assert any(item.entity_type == "job" and "completed" in (item.summary or "").lower() for item in entities)
        artifacts = await persistence.list_artifacts(session.id)
        assert any(item.kind == "spliceai_result" for item in artifacts)

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.chat_message import ChatMessage
from app.models.chat_tool_call import ChatToolCall
from app.models.session_artifact import SessionArtifact
from app.models.session_entity import SessionEntity
from app.models.spliceai_job import SpliceAIJob
from app.schemas.bio_evidence import BioEvidence, evidence_from_dict
from app.schemas.spliceai import SpliceAIResult
from app.services.bio_evidence import evidence_from_spliceai_result, evidence_from_variant
from app.services.session_history import SessionHistoryService


def _relative_storage_path(full_path: str) -> str:
    root = Path(get_settings().script_workspace_root).resolve()
    candidate = Path(full_path).resolve()
    try:
        return str(candidate.relative_to(root))
    except ValueError:
        return full_path


def _truncate_json(value: dict | None, max_chars: int = 4000) -> dict | None:
    if value is None:
        return None
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= max_chars:
        return value
    return {"_truncated": True, "preview": text[:max_chars]}


def _output_ref_from_payload(tool_name: str, output: dict | None) -> str | None:
    if not output:
        return None
    if job_id := output.get("job_id"):
        return f"spliceai:job:{job_id}"
    if run_id := output.get("run_id"):
        script_name = output.get("script_name") or "script"
        return f"script:{script_name}:{run_id}"
    if output_dir := output.get("output_dir"):
        return _relative_storage_path(str(output_dir))
    evidence = output.get("evidence")
    if isinstance(evidence, dict) and evidence.get("raw_ref"):
        return str(evidence["raw_ref"])
    return None


def _canonical_id_from_evidence(evidence: BioEvidence) -> str:
    ids = evidence.identifiers
    if evidence.entity_type == "variant":
        return ids.get("variant_hgvs") or evidence.raw_ref or "unknown-variant"
    if evidence.entity_type == "job":
        return ids.get("job_id") or ids.get("run_id") or evidence.raw_ref or "unknown-job"
    if evidence.entity_type == "literature":
        return ids.get("top_id") or ids.get("term") or evidence.raw_ref or "unknown-literature"
    if evidence.entity_type == "protein":
        return ids.get("accession") or ids.get("query") or evidence.raw_ref or "unknown-protein"
    if evidence.entity_type == "gene":
        return ids.get("gene") or ids.get("term") or evidence.raw_ref or "unknown-gene"
    return evidence.raw_ref or json.dumps(ids, sort_keys=True, ensure_ascii=False)


def _display_name_from_evidence(evidence: BioEvidence) -> str | None:
    ids = evidence.identifiers
    for key in ("variant_hgvs", "job_id", "accession", "gene", "term", "top_id"):
        if ids.get(key):
            return ids[key]
    return evidence.summary


class SessionPersistenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.history = SessionHistoryService(db)

    async def _current_turn_index(self, session_id: str) -> int:
        result = await self.db.execute(
            select(func.max(ChatMessage.turn_index)).where(ChatMessage.session_id == session_id)
        )
        current = result.scalar_one_or_none()
        return int(current or 0)

    async def record_tool_call(
        self,
        *,
        session_id: str,
        task_id: str,
        trace_id: str | None,
        tool_name: str,
        call_id: str | None,
        input_json: dict,
        output_json: dict | None,
        output_ref: str | None,
        status: str,
        error_code: str | None,
        duration_ms: int | None,
    ) -> ChatToolCall:
        turn_index = await self._current_turn_index(session_id)
        record = ChatToolCall(
            id=str(uuid4()),
            session_id=session_id,
            turn_index=turn_index,
            task_id=task_id,
            trace_id=trace_id,
            tool_name=tool_name,
            call_id=call_id or str(uuid4()),
            input_json=_truncate_json(input_json) or {},
            output_json=_truncate_json(output_json),
            output_ref=output_ref,
            status=status,
            error_code=error_code,
            duration_ms=duration_ms,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def upsert_entity_from_evidence(
        self,
        *,
        session_id: str,
        turn_index: int,
        tool_call_id: str,
        evidence: BioEvidence,
        mark_active: bool = True,
    ) -> SessionEntity:
        canonical_id = _canonical_id_from_evidence(evidence)
        result = await self.db.execute(
            select(SessionEntity).where(
                SessionEntity.session_id == session_id,
                SessionEntity.entity_type == evidence.entity_type,
                SessionEntity.canonical_id == canonical_id,
            )
        )
        existing = result.scalars().first()
        if mark_active:
            active_rows = await self.db.execute(
                select(SessionEntity).where(SessionEntity.session_id == session_id, SessionEntity.is_active.is_(True))
            )
            for row in active_rows.scalars().all():
                if row.entity_type == evidence.entity_type:
                    row.is_active = False

        if existing:
            existing.display_name = _display_name_from_evidence(evidence)
            existing.genome_build = evidence.genome_build
            existing.source = evidence.source
            existing.source_turn = turn_index
            existing.source_tool_call_id = tool_call_id
            existing.confidence = evidence.confidence
            existing.summary = evidence.summary
            existing.raw_ref = evidence.raw_ref
            existing.metadata_json = evidence.identifiers
            existing.is_active = mark_active
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        entity = SessionEntity(
            session_id=session_id,
            entity_type=evidence.entity_type,
            canonical_id=canonical_id,
            display_name=_display_name_from_evidence(evidence),
            genome_build=evidence.genome_build,
            source=evidence.source,
            source_turn=turn_index,
            source_tool_call_id=tool_call_id,
            confidence=evidence.confidence,
            summary=evidence.summary,
            raw_ref=evidence.raw_ref,
            metadata_json=evidence.identifiers,
            is_active=mark_active,
        )
        self.db.add(entity)
        await self.db.commit()
        await self.db.refresh(entity)
        return entity

    async def link_spliceai_job(
        self,
        *,
        job_id: str,
        turn_index: int,
        tool_call_id: str,
    ) -> None:
        result = await self.db.execute(select(SpliceAIJob).where(SpliceAIJob.id == job_id))
        job = result.scalars().first()
        if job is None:
            return
        job.turn_index = turn_index
        job.tool_call_id = tool_call_id
        await self.db.commit()

    async def ingest_tool_output(
        self,
        *,
        session_id: str,
        task_id: str,
        turn_index: int,
        tool_call_id: str,
        tool_name: str,
        output: dict | None,
    ) -> None:
        if not output:
            return
        evidence = evidence_from_dict(output.get("evidence", {}))
        if evidence:
            await self.upsert_entity_from_evidence(
                session_id=session_id,
                turn_index=turn_index,
                tool_call_id=tool_call_id,
                evidence=evidence,
                mark_active=evidence.entity_type in {"variant", "job"},
            )
        if tool_name == "bio_spliceai_submit" and output.get("job_id"):
            await self.link_spliceai_job(
                job_id=str(output["job_id"]),
                turn_index=turn_index,
                tool_call_id=tool_call_id,
            )
            retrieved_at = str(output.get("retrieved_at") or "")
            if not retrieved_at and isinstance(output.get("evidence"), dict):
                retrieved_at = str(output["evidence"].get("retrieved_at") or "")
            variant_hgvs = ""
            if isinstance(output.get("evidence"), dict):
                variant_hgvs = str(output["evidence"].get("identifiers", {}).get("variant_hgvs") or "")
            if variant_hgvs:
                await self.upsert_entity_from_evidence(
                    session_id=session_id,
                    turn_index=turn_index,
                    tool_call_id=tool_call_id,
                    evidence=evidence_from_variant(
                        variant_hgvs=variant_hgvs,
                        genome_build=str(output.get("genome_build") or "GRCh38"),
                        source="spliceai",
                        retrieved_at=retrieved_at or "unknown",
                        summary=f"Submitted for SpliceAI scoring ({output.get('job_id')})",
                    ),
                    mark_active=True,
                )
        if tool_name == "bio_script_runner":
            await self.record_script_artifacts(
                session_id=session_id,
                task_id=task_id,
                turn_index=turn_index,
                output=output,
            )

    async def record_artifact(
        self,
        *,
        session_id: str,
        task_id: str,
        turn_index: int | None,
        kind: str,
        storage_path: str,
        filename: str | None = None,
        run_id: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        metadata: dict | None = None,
    ) -> SessionArtifact:
        artifact = SessionArtifact(
            id=str(uuid4()),
            session_id=session_id,
            turn_index=turn_index,
            task_id=task_id,
            run_id=run_id,
            kind=kind,
            filename=filename,
            storage_path=_relative_storage_path(storage_path),
            mime_type=mime_type,
            sha256=None,
            size_bytes=size_bytes,
            metadata_json=metadata or {},
        )
        file_path = Path(storage_path)
        if file_path.is_file():
            artifact.size_bytes = file_path.stat().st_size
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            artifact.sha256 = digest
        self.db.add(artifact)
        await self.db.commit()
        await self.db.refresh(artifact)
        return artifact

    async def record_script_artifacts(
        self,
        *,
        session_id: str,
        task_id: str,
        turn_index: int,
        output: dict,
    ) -> None:
        output_dir = output.get("output_dir")
        if not output_dir or not session_id:
            return
        run_id = str(output.get("run_id") or "")
        base_meta = {
            "script_name": output.get("script_name"),
            "exit_code": output.get("exit_code"),
            "sandbox_mode": output.get("sandbox_mode"),
        }
        for filename, kind, mime in (
            ("meta.json", "script_output", "application/json"),
            ("stdout.txt", "script_output", "text/plain"),
            ("stderr.txt", "script_output", "text/plain"),
        ):
            file_path = Path(output_dir) / filename
            if not file_path.exists():
                continue
            await self.record_artifact(
                session_id=session_id,
                task_id=task_id,
                turn_index=turn_index,
                kind=kind,
                storage_path=str(file_path),
                filename=filename,
                run_id=run_id or None,
                mime_type=mime,
                metadata={**base_meta, "artifact_file": filename},
            )

    async def sync_spliceai_job_completion(
        self,
        *,
        job: SpliceAIJob,
        result: SpliceAIResult | None = None,
        error_message: str | None = None,
    ) -> None:
        if not job.session_id:
            return
        turn_index = job.turn_index or await self._current_turn_index(job.session_id)
        tool_call_id = job.tool_call_id or ""
        retrieved_at = datetime.now(timezone.utc).isoformat()

        await self.upsert_entity_from_evidence(
            session_id=job.session_id,
            turn_index=turn_index,
            tool_call_id=tool_call_id,
            evidence=evidence_from_variant(
                variant_hgvs=job.variant_hgvs,
                genome_build=job.genome_build,
                source="spliceai",
                retrieved_at=retrieved_at,
                summary=f"Variant under analysis ({job.status})",
            ),
            mark_active=True,
        )

        if result is not None:
            evidence = evidence_from_spliceai_result(
                job_id=job.id,
                variant_hgvs=job.variant_hgvs,
                genome_build=job.genome_build,
                result=result,
                retrieved_at=retrieved_at,
            )
            await self.upsert_entity_from_evidence(
                session_id=job.session_id,
                turn_index=turn_index,
                tool_call_id=tool_call_id,
                evidence=evidence,
                mark_active=True,
            )
            await self.record_artifact(
                session_id=job.session_id,
                task_id=job.trace_id or job.id,
                turn_index=turn_index,
                kind="spliceai_result",
                storage_path=f"spliceai/jobs/{job.id}/result.json",
                filename="result.json",
                run_id=job.id,
                mime_type="application/json",
                metadata={
                    "job_id": job.id,
                    "status": "success",
                    "variant_hgvs": job.variant_hgvs,
                    "max_score": result.score_breakdown.max_score,
                    "predicted_impact": result.predicted_impact,
                    "archived_result": result.model_dump(mode="python"),
                },
            )
            return

        failed_summary = (error_message or job.error_message or "SpliceAI job failed")[:500]
        await self.upsert_entity_from_evidence(
            session_id=job.session_id,
            turn_index=turn_index,
            tool_call_id=tool_call_id,
            evidence=BioEvidence(
                source="spliceai",
                entity_type="job",
                identifiers={"job_id": job.id, "variant_hgvs": job.variant_hgvs, "status": "failed"},
                genome_build=job.genome_build,
                retrieved_at=retrieved_at,
                confidence=0.0,
                summary=failed_summary,
                raw_ref=f"spliceai:job:{job.id}",
            ),
            mark_active=True,
        )

    async def list_artifacts(self, session_id: str, limit: int = 100) -> list[SessionArtifact]:
        result = await self.db.execute(
            select(SessionArtifact)
            .where(SessionArtifact.session_id == session_id)
            .order_by(SessionArtifact.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    def format_entities_block(self, entities: list[SessionEntity]) -> str:
        if not entities:
            return ""
        lines: list[str] = []
        for item in entities[:8]:
            label = item.display_name or item.canonical_id
            extra = f" status_ref={item.raw_ref}" if item.raw_ref else ""
            if item.summary:
                extra += f" summary={item.summary[:120]}"
            lines.append(f"- {item.entity_type}: {label}{extra}")
        return "[ActiveAnalysis]\n" + "\n".join(lines)

    def format_tool_calls_block(self, tool_calls: list[ChatToolCall]) -> str:
        if not tool_calls:
            return ""
        lines: list[str] = []
        for item in tool_calls[-10:]:
            ref = f" ref={item.output_ref}" if item.output_ref else ""
            lines.append(f"- turn={item.turn_index} {item.tool_name} [{item.status}]{ref}")
        return "[RecentToolCalls]\n" + "\n".join(lines)

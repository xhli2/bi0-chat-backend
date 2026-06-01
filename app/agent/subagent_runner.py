from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.agent.hook_registry import HookEvent
from app.agent.hooks import emit_hook
from app.schemas.bio_evidence import BioEvidencePack
from app.schemas.realtime import AgentEvent
from app.services.task_manager import task_manager
from app.tools.executor import ToolExecutor
from app.tools.schemas import ToolExecutionContext


SUBAGENT_TOOL_POLICIES: dict[str, set[str]] = {
    "research_worker": {
        "bio_ncbi_search",
        "bio_uniprot_lookup",
        "session_lookup",
        "summarize_chunk",
    },
    "analysis_worker": {
        "bio_spliceai_submit",
        "bio_spliceai_get_result",
        "bio_script_runner",
        "summarize_chunk",
    },
    "report_worker": {
        "summarize_chunk",
        "session_lookup",
        "bio_script_runner",
    },
}


@dataclass
class SubagentResult:
    role: str
    output: str
    evidence_block: str = ""
    child_task_id: str = ""
    subagent_id: str = ""
    tools_executed: list[str] | None = None


def _build_tool_args(tool_name: str, prompt: str) -> dict[str, Any]:
    snippet = prompt.strip()[:200] or "bio"
    if tool_name == "bio_ncbi_search":
        return {"term": snippet, "db": "pubmed", "retmax": 5}
    if tool_name == "bio_uniprot_lookup":
        return {"query": snippet, "size": 5}
    if tool_name == "summarize_chunk":
        return {"text": prompt[:1000], "max_chars": 400}
    if tool_name == "session_lookup":
        return {"message_limit": 5}
    return {}


async def run_subagent_step(
    *,
    parent_task_id: str,
    role: str,
    prompt: str,
    trace_id: str | None,
    tenant_id: str,
    session_id: str | None,
    user_id: int | None,
    permissions: set[str],
    scopes: set[str],
    approved_tools: set[str],
    step_tools: list[str],
    executor: ToolExecutor,
) -> SubagentResult:
    subagent_id = str(uuid4())
    child_task_id = await task_manager.create_task(
        priority="low",
        model="builtin",
        tenant_id=tenant_id,
        trace_id=trace_id,
        session_id=session_id,
        user_id=user_id,
        owner_id=user_id,
        current_operator_id=user_id,
    )

    allowed_tools = SUBAGENT_TOOL_POLICIES.get(role, set())
    candidate_tools = [tool for tool in step_tools if tool in allowed_tools]
    if not candidate_tools:
        candidate_tools = sorted(allowed_tools & {"bio_ncbi_search", "bio_uniprot_lookup", "summarize_chunk"})

    emit_hook(
        HookEvent.SUBAGENT_START,
        tenant_id=tenant_id,
        task_id=child_task_id,
        parent_task_id=parent_task_id,
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        subagent_id=subagent_id,
        subagent_role=role,
        metadata={"allowed_tools": sorted(allowed_tools), "candidate_tools": candidate_tools},
    )

    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="subagent_started",
            task_id=parent_task_id,
            payload={
                "subagent_id": subagent_id,
                "child_task_id": child_task_id,
                "parent_task_id": parent_task_id,
                "role": role,
                "prompt_preview": prompt[:300],
                "allowed_tools": sorted(allowed_tools),
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
            },
        )
    )

    context = ToolExecutionContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
        task_id=child_task_id,
        permissions=permissions,
        scopes=scopes,
        approved_tools=approved_tools,
        allowed_tools=allowed_tools,
    )

    evidence_pack = BioEvidencePack()
    summaries: list[str] = []
    tools_executed: list[str] = []

    for tool_name in candidate_tools[:3]:
        hook_meta = {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "task_id": child_task_id,
            "trace_id": trace_id,
            "allowed_tools": sorted(allowed_tools),
        }
        result = await executor.execute(tool_name, _build_tool_args(tool_name, prompt), context)
        if not result.ok:
            summaries.append(f"{tool_name}: {result.error_code} - {result.message}")
            continue
        tools_executed.append(tool_name)
        data = result.data if isinstance(result.data, dict) else {}
        if isinstance(data.get("evidence"), dict):
            from app.schemas.bio_evidence import evidence_from_dict

            item = evidence_from_dict(data["evidence"])
            if item is not None:
                evidence_pack.add(item)
        if tool_name == "bio_ncbi_search":
            summaries.append(f"NCBI found {data.get('total_count', 0)} hits for '{data.get('term', '')}'")
        elif tool_name == "bio_uniprot_lookup":
            records = data.get("records") or []
            summaries.append(f"UniProt matched {len(records)} record(s)")
        elif tool_name == "summarize_chunk":
            summaries.append(str(data.get("summary", ""))[:300])
        else:
            summaries.append(f"{tool_name} completed")

    output = (
        f"[Subagent:{role}:{subagent_id}] "
        + ("; ".join(summaries) if summaries else "No tools executed successfully.")
    )
    evidence_block = evidence_pack.to_context_block()

    emit_hook(
        HookEvent.SUBAGENT_STOP,
        tenant_id=tenant_id,
        task_id=child_task_id,
        parent_task_id=parent_task_id,
        session_id=session_id,
        trace_id=trace_id,
        subagent_id=subagent_id,
        subagent_role=role,
        metadata={"tools_executed": tools_executed},
    )

    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="subagent_completed",
            task_id=parent_task_id,
            payload={
                "subagent_id": subagent_id,
                "child_task_id": child_task_id,
                "parent_task_id": parent_task_id,
                "role": role,
                "output_preview": output[:300],
                "tools_executed": tools_executed,
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
            },
        )
    )

    return SubagentResult(
        role=role,
        output=output,
        evidence_block=evidence_block,
        child_task_id=child_task_id,
        subagent_id=subagent_id,
        tools_executed=tools_executed,
    )

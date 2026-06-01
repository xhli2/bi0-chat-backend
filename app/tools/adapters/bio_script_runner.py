from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.script_runner import run_script_in_workspace
from app.tools.schemas import ToolExecutionError


class BioScriptRunInput(BaseModel):
    script_name: str = Field(min_length=1, max_length=128)
    runtime: str = Field(default="python", pattern="^(python|bash)$")
    args: list[str] = Field(default_factory=list, max_length=20)


class BioScriptRunOutput(BaseModel):
    script_name: str
    runtime: str
    args: list[str]
    exit_code: int
    stdout_preview: str
    stderr_preview: str
    workspace_path: str
    output_dir: str
    run_id: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


async def tool_bio_script_runner(args: dict[str, Any], runtime_ctx: dict[str, Any]) -> dict[str, Any]:
    payload = BioScriptRunInput.model_validate(args)
    context = runtime_ctx.get("context") if isinstance(runtime_ctx, dict) else None
    if context is None:
        raise ToolExecutionError("MISSING_CONTEXT", "Tool runtime context is required.")

    if len(payload.args) > 20:
        raise ToolExecutionError("SCRIPT_ARGS_TOO_MANY", "Too many script arguments.")

    output = await run_script_in_workspace(
        tenant_id=context.tenant_id,
        session_id=context.session_id,
        task_id=context.task_id,
        trace_id=context.trace_id,
        script_name=payload.script_name,
        runtime=payload.runtime,
        args=payload.args,
        hook_metadata={
            "tenant_id": context.tenant_id,
            "session_id": context.session_id,
            "task_id": context.task_id,
            "trace_id": context.trace_id,
            "allowed_tools": sorted(context.allowed_tools) if context.allowed_tools else None,
        },
    )
    return BioScriptRunOutput.model_validate(output).model_dump(mode="python")

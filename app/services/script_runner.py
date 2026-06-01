from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.agent.hooks import emit_hook
from app.agent.hook_registry import HookEvent
from app.core.config import get_settings
from app.services.bio_evidence import attach_evidence
from app.services.script_sandbox import run_in_sandbox
from app.services.script_workspace import output_run_dir, script_path
from app.services.skill_environment import is_script_allowed_in_workspace
from app.schemas.bio_evidence import BioEvidence
from app.tools.schemas import ToolExecutionError


async def run_script_in_workspace(
    *,
    tenant_id: str,
    session_id: str | None,
    task_id: str,
    trace_id: str | None,
    script_name: str,
    runtime: str,
    args: list[str],
    hook_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    session_key = session_id or "no-session"
    run_id = str(uuid4())
    workspace = script_path(
        tenant_id=tenant_id,
        session_id=session_key,
        task_id=task_id,
        script_name=script_name,
    )
    if not workspace.exists():
        raise ToolExecutionError("SCRIPT_NOT_FOUND", f"Script not found: {script_name}")
    if not is_script_allowed_in_workspace(
        tenant_id=tenant_id,
        session_id=session_id,
        task_id=task_id,
        script_name=script_name,
    ):
        raise ToolExecutionError(
            "SCRIPT_NOT_ALLOWED",
            f"Script '{script_name}' is not registered in workspace skill manifest.",
        )

    tool_args = {"script_name": script_name, "runtime": runtime, "args": args}
    meta = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "task_id": task_id,
        "trace_id": trace_id,
        **(hook_metadata or {}),
    }
    emit_hook(
        HookEvent.PRE_SCRIPT_RUN,
        tenant_id=tenant_id,
        task_id=task_id,
        session_id=session_id,
        trace_id=trace_id,
        script_name=script_name,
        workspace_path=str(workspace.parent.parent),
        tool_args=tool_args,
        metadata=meta,
    )

    workspace_root_path = workspace.parent.parent
    out_dir = output_run_dir(tenant_id=tenant_id, session_id=session_key, task_id=task_id, run_id=run_id)
    sandbox_result = await run_in_sandbox(
        workspace_root=workspace_root_path,
        script_path=workspace,
        output_dir=out_dir,
        runtime=runtime,
        args=args,
        tenant_id=tenant_id,
        task_id=task_id,
    )
    stdout_preview = sandbox_result["stdout_preview"]
    stderr_preview = sandbox_result["stderr_preview"]
    (out_dir / "stdout.txt").write_text(sandbox_result["stdout"], encoding="utf-8")
    (out_dir / "stderr.txt").write_text(sandbox_result["stderr"], encoding="utf-8")
    meta_payload = {
        "script_name": script_name,
        "runtime": runtime,
        "args": args,
        "exit_code": sandbox_result["returncode"],
        "run_id": run_id,
        "sandbox_mode": sandbox_result.get("sandbox_mode"),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    output = {
        "script_name": script_name,
        "runtime": runtime,
        "args": args,
        "exit_code": sandbox_result["returncode"],
        "stdout_preview": stdout_preview,
        "stderr_preview": stderr_preview,
        "workspace_path": str(workspace_root_path),
        "output_dir": str(out_dir),
        "run_id": run_id,
        "sandbox_mode": sandbox_result.get("sandbox_mode"),
        "retrieved_at": retrieved_at,
    }
    output = attach_evidence(
        output,
        BioEvidence(
            source="bio_script_runner",
            entity_type="job",
            identifiers={
                "script_name": script_name,
                "run_id": run_id,
                "exit_code": str(sandbox_result["returncode"]),
                "sandbox_mode": str(sandbox_result.get("sandbox_mode", "local")),
            },
            retrieved_at=retrieved_at,
            confidence=1.0 if sandbox_result["returncode"] == 0 else 0.2,
            summary=f"Script {script_name} finished with exit code {sandbox_result['returncode']}",
            raw_ref=f"script:{script_name}:{run_id}",
        ),
    )

    emit_hook(
        HookEvent.POST_SCRIPT_RUN,
        tenant_id=tenant_id,
        task_id=task_id,
        session_id=session_id,
        trace_id=trace_id,
        script_name=script_name,
        workspace_path=str(workspace.parent.parent),
        tool_output=output,
        metadata=meta,
    )
    return output

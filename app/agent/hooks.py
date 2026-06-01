from __future__ import annotations

import hashlib
import re
from typing import Any

from app.agent.hook_registry import HookContext, HookEvent, hook_registry
from app.services.skill_environment import is_script_allowed_in_workspace, register_skill_environment_hooks
from app.core.tool_errors import ToolExecutionError

_HGVS_RE = re.compile(
    r"^(?:NC_|NM_|NP_|NR_|ENST|chr)?[A-Za-z0-9._:-]+(?::c\.|\.c\.|:g\.|\.g\.|:p\.|\.p\.|:n\.|\.n\.)[A-Za-z0-9*>+\-=\(\)]+$",
    re.IGNORECASE,
)
_SIMPLE_VARIANT_RE = re.compile(r"^[A-Z0-9]{1,10}:[cgp]\.[A-Za-z0-9*>+\-=\(\)]+$", re.IGNORECASE)
_SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def validate_hgvs(value: str) -> None:
    candidate = value.strip()
    if len(candidate) < 5:
        raise ToolExecutionError("HGVS_INVALID", "variant_hgvs is too short.")
    if "\n" in candidate or "\r" in candidate:
        raise ToolExecutionError("HGVS_INVALID", "variant_hgvs must not contain newlines.")
    if _HGVS_RE.match(candidate) or _SIMPLE_VARIANT_RE.match(candidate):
        return
    if ":" in candidate and ("c." in candidate or "g." in candidate or "p." in candidate):
        return
    raise ToolExecutionError("HGVS_INVALID", f"Invalid HGVS format: {candidate[:80]}")


def _validate_script_name(script_name: str) -> None:
    if not _SCRIPT_NAME_RE.fullmatch(script_name):
        raise ToolExecutionError("SCRIPT_NAME_INVALID", "script_name must be 1-128 chars: letters, digits, dot, underscore, hyphen.")


def _pre_tool_use(context: HookContext) -> None:
    if context.tool_name == "bio_spliceai_submit":
        variant_hgvs = context.tool_args.get("variant_hgvs")
        if isinstance(variant_hgvs, str):
            validate_hgvs(variant_hgvs)
    if context.tool_name == "bio_ensembl_vep":
        variant_hgvs = context.tool_args.get("variant_hgvs")
        if isinstance(variant_hgvs, str):
            validate_hgvs(variant_hgvs)
    allowed_tools = context.metadata.get("allowed_tools")
    if allowed_tools and context.tool_name and context.tool_name not in allowed_tools:
        raise ToolExecutionError(
            "SUBAGENT_TOOL_FORBIDDEN",
            f"Tool '{context.tool_name}' is not allowed for this subagent.",
        )


def _post_tool_use(context: HookContext) -> None:
    output = context.tool_output or {}
    if context.tool_name == "bio_spliceai_submit":
        genome_build = output.get("genome_build")
        if genome_build and genome_build not in {"GRCh37", "GRCh38"}:
            raise ToolExecutionError("GENOME_BUILD_INVALID", f"Unsupported genome build: {genome_build}")
    if "evidence" in output and output["evidence"] is not None:
        evidence = output["evidence"]
        if not isinstance(evidence, dict):
            raise ToolExecutionError("EVIDENCE_SCHEMA_INVALID", "Tool evidence must be an object.")
        for field in ("source", "entity_type", "retrieved_at"):
            if field not in evidence:
                raise ToolExecutionError("EVIDENCE_SCHEMA_INVALID", f"Missing evidence field: {field}")


def _pre_script_run(context: HookContext) -> None:
    script_name = context.script_name or context.tool_args.get("script_name")
    if isinstance(script_name, str):
        _validate_script_name(script_name)
    runtime = context.tool_args.get("runtime", "python")
    if runtime not in {"python", "bash"}:
        raise ToolExecutionError("SCRIPT_RUNTIME_INVALID", f"Unsupported runtime: {runtime}")
    if ".." in str(script_name):
        raise ToolExecutionError("SCRIPT_PATH_TRAVERSAL", "Path traversal is not allowed in script_name.")
    if isinstance(script_name, str) and context.task_id:
        if not is_script_allowed_in_workspace(
            tenant_id=context.tenant_id,
            session_id=context.session_id,
            task_id=context.task_id,
            script_name=script_name,
        ):
            raise ToolExecutionError(
                "SCRIPT_NOT_ALLOWED",
                f"Script '{script_name}' is not registered in workspace skill manifest.",
            )


def _post_script_run(context: HookContext) -> None:
    output = context.tool_output or {}
    exit_code = output.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        raise ToolExecutionError(
            "SCRIPT_EXIT_NONZERO",
            f"Script exited with code {exit_code}: {output.get('stderr_preview', '')[:200]}",
            retryable=False,
        )


def _subagent_start(context: HookContext) -> None:
    role = context.subagent_role or ""
    if role == "research_worker" and context.metadata.get("requested_tool") == "bio_script_runner":
        raise ToolExecutionError("SUBAGENT_TOOL_FORBIDDEN", "research_worker cannot run bio_script_runner.")


def register_default_hooks() -> None:
    if hook_registry._handlers[HookEvent.PRE_TOOL_USE]:
        return
    hook_registry.register(HookEvent.PRE_TOOL_USE, _pre_tool_use)
    hook_registry.register(HookEvent.POST_TOOL_USE, _post_tool_use)
    hook_registry.register(HookEvent.PRE_SCRIPT_RUN, _pre_script_run)
    hook_registry.register(HookEvent.POST_SCRIPT_RUN, _post_script_run)
    hook_registry.register(HookEvent.SUBAGENT_START, _subagent_start)
    register_skill_environment_hooks()


def _hook_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return metadata or {}


def run_pre_tool_hooks(tool_name: str, args: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> None:
    register_default_hooks()
    meta = _hook_metadata(metadata)
    hook_registry.emit(
        HookContext(
            event=HookEvent.PRE_TOOL_USE,
            tenant_id=str(meta.get("tenant_id", "public")),
            task_id=str(meta.get("task_id", "unknown")),
            session_id=meta.get("session_id"),
            trace_id=meta.get("trace_id"),
            tool_name=tool_name,
            tool_args=args,
            metadata=meta,
        )
    )


def run_post_tool_hooks(tool_name: str, output: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> None:
    register_default_hooks()
    meta = _hook_metadata(metadata)
    hook_registry.emit(
        HookContext(
            event=HookEvent.POST_TOOL_USE,
            tenant_id=str(meta.get("tenant_id", "public")),
            task_id=str(meta.get("task_id", "unknown")),
            session_id=meta.get("session_id"),
            trace_id=meta.get("trace_id"),
            tool_name=tool_name,
            tool_output=output,
            metadata=meta,
        )
    )


def emit_hook(event: HookEvent, **kwargs: Any) -> None:
    register_default_hooks()
    metadata = kwargs.pop("metadata", {})
    hook_registry.emit(HookContext(event=event, metadata=metadata, **kwargs))


def script_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

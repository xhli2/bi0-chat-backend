from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.agent.hook_registry import HookContext, HookEvent, hook_registry
from app.agent.skill_specs import DEFAULT_SKILL_SPECS, SkillSpec, get_skill_spec
from app.core.config import get_settings
from app.services.script_workspace import ensure_workspace, workspace_root


def skills_root() -> Path:
    settings = get_settings()
    root = Path(settings.skills_root)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root


def skill_dir(skill_name: str) -> Path:
    return skills_root() / skill_name


def load_skill_manifest(skill_name: str) -> dict[str, Any]:
    spec = get_skill_spec(skill_name)
    manifest_path = skill_dir(skill_name) / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scripts = manifest.get("scripts")
    if not scripts and spec and spec.bundled_scripts:
        manifest["scripts"] = list(spec.bundled_scripts)
    if not manifest.get("default_script") and spec and spec.default_script:
        manifest["default_script"] = spec.default_script
    return manifest


def load_skill_instructions_from_disk(skill_name: str) -> str | None:
    skill_md = skill_dir(skill_name) / "SKILL.md"
    if not skill_md.exists():
        return None
    return skill_md.read_text(encoding="utf-8").strip()


def bundled_script_sources(skill_name: str) -> dict[str, Path]:
    manifest = load_skill_manifest(skill_name)
    script_names = manifest.get("scripts") or []
    sources: dict[str, Path] = {}
    scripts_dir = skill_dir(skill_name) / "scripts"
    for name in script_names:
        if not isinstance(name, str):
            continue
        candidate = (scripts_dir / name).resolve()
        if scripts_dir.resolve() not in candidate.parents:
            continue
        if candidate.exists():
            sources[name] = candidate
    return sources


def materialize_skills_to_workspace(
    *,
    skill_names: list[str],
    tenant_id: str,
    session_id: str,
    task_id: str,
) -> list[str]:
    settings = get_settings()
    if not settings.skill_materialize_on_session_start:
        return []

    root = ensure_workspace(tenant_id=tenant_id, session_id=session_id, task_id=task_id)
    scripts_dir = root / "scripts"
    copied: list[str] = []
    manifest: dict[str, Any] = {"skills": {}, "scripts": {}}

    for skill_name in skill_names:
        spec = get_skill_spec(skill_name)
        if spec is None:
            continue
        sources = bundled_script_sources(skill_name)
        if not sources:
            continue
        manifest["skills"][skill_name] = {
            "default_script": spec.default_script or load_skill_manifest(skill_name).get("default_script"),
            "subagent_role": spec.subagent_role,
        }
        for script_name, source_path in sources.items():
            target = scripts_dir / script_name
            if not target.exists():
                shutil.copy2(source_path, target)
            copied.append(script_name)
            manifest["scripts"][script_name] = {
                "skill": skill_name,
                "source": str(source_path),
            }

    if manifest["scripts"]:
        (root / ".skill_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return sorted(set(copied))


def read_workspace_manifest(*, tenant_id: str, session_id: str, task_id: str) -> dict[str, Any]:
    root = workspace_root(tenant_id=tenant_id, session_id=session_id, task_id=task_id)
    manifest_path = root / ".skill_manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def is_script_allowed_in_workspace(
    *,
    tenant_id: str,
    session_id: str | None,
    task_id: str,
    script_name: str,
) -> bool:
    settings = get_settings()
    if not settings.skill_script_allowlist_enabled:
        return True
    session_key = session_id or "no-session"
    manifest = read_workspace_manifest(tenant_id=tenant_id, session_id=session_key, task_id=task_id)
    allowed = set((manifest.get("scripts") or {}).keys())
    if allowed:
        return script_name in allowed
    # User-uploaded scripts without skill manifest remain allowed when allowlist is empty.
    return True


def cleanup_task_workspace(*, tenant_id: str, session_id: str, task_id: str) -> None:
    root = workspace_root(tenant_id=tenant_id, session_id=session_id, task_id=task_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def cleanup_session_workspaces(*, tenant_id: str, session_id: str) -> None:
    session_dir = Path(get_settings().script_workspace_root)
    if not session_dir.is_absolute():
        session_dir = Path(__file__).resolve().parents[2] / session_dir
    session_dir = session_dir / tenant_id / (session_id or "no-session")
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


def resolve_specs_by_names(skill_names: list[str]) -> list[SkillSpec]:
    by_name = {spec.name: spec for spec in DEFAULT_SKILL_SPECS}
    return [by_name[name] for name in skill_names if name in by_name]


def _session_start_materialize(context: HookContext) -> None:
    skill_names = context.metadata.get("skill_names") or []
    if not skill_names or not context.session_id:
        return
    materialize_skills_to_workspace(
        skill_names=list(skill_names),
        tenant_id=context.tenant_id,
        session_id=context.session_id,
        task_id=context.task_id,
    )


def _session_stop_cleanup(context: HookContext) -> None:
    settings = get_settings()
    if not settings.workspace_cleanup_on_session_stop or not context.session_id:
        return
    cleanup_session_workspaces(tenant_id=context.tenant_id, session_id=context.session_id)


def register_skill_environment_hooks() -> None:
    hook_registry.register(HookEvent.SESSION_START, _session_start_materialize)
    if get_settings().workspace_cleanup_on_session_stop:
        hook_registry.register(HookEvent.SESSION_STOP, _session_stop_cleanup)

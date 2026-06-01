from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def workspace_root(*, tenant_id: str, session_id: str, task_id: str) -> Path:
    settings = get_settings()
    root = Path(settings.script_workspace_root)
    safe_session = session_id or "no-session"
    return root / tenant_id / safe_session / task_id


def ensure_workspace(*, tenant_id: str, session_id: str, task_id: str) -> Path:
    root = workspace_root(tenant_id=tenant_id, session_id=session_id, task_id=task_id)
    for subdir in ("scripts", "inputs", "outputs"):
        (root / subdir).mkdir(parents=True, exist_ok=True)
    return root


def script_path(*, tenant_id: str, session_id: str, task_id: str, script_name: str) -> Path:
    root = ensure_workspace(tenant_id=tenant_id, session_id=session_id, task_id=task_id)
    candidate = (root / "scripts" / script_name).resolve()
    scripts_dir = (root / "scripts").resolve()
    if scripts_dir not in candidate.parents and candidate != scripts_dir:
        raise ValueError("script path escapes workspace scripts directory")
    return candidate


def output_run_dir(*, tenant_id: str, session_id: str, task_id: str, run_id: str) -> Path:
    root = ensure_workspace(tenant_id=tenant_id, session_id=session_id, task_id=task_id)
    path = (root / "outputs" / run_id).resolve()
    outputs_dir = (root / "outputs").resolve()
    if outputs_dir not in path.parents:
        raise ValueError("output path escapes workspace outputs directory")
    path.mkdir(parents=True, exist_ok=True)
    return path

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.tools.schemas import ToolExecutionError


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def execute_local(
    *,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> SandboxResult:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    return SandboxResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def execute_docker(
    *,
    workspace_root: Path,
    output_dir: Path,
    script_path: Path,
    runtime: str,
    args: list[str],
    env: dict[str, str],
    timeout: int,
) -> SandboxResult:
    settings = get_settings()
    if shutil.which("docker") is None:
        raise ToolExecutionError(
            "DOCKER_NOT_AVAILABLE",
            "script_runner_mode=docker but docker CLI is not installed.",
        )

    scripts_dir = workspace_root / "scripts"
    script_in_container = f"/workspace/scripts/{script_path.name}"
    if runtime == "python":
        inner = ["python", script_in_container, *args]
    else:
        inner = ["bash", script_in_container, *args]

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        settings.script_runner_docker_network,
        "--memory",
        settings.script_runner_docker_memory,
        "-v",
        f"{workspace_root.resolve()}:/workspace:ro",
        "-v",
        f"{output_dir.resolve()}:/output:rw",
        "-w",
        "/workspace",
        "-e",
        "BIO_WORKSPACE=/workspace",
        "-e",
        "BIO_OUTPUT_DIR=/output",
        "-e",
        f"BIO_TENANT_ID={env.get('BIO_TENANT_ID', '')}",
        "-e",
        f"BIO_TASK_ID={env.get('BIO_TASK_ID', '')}",
        settings.script_runner_docker_image,
        *inner,
    ]

    completed = subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return SandboxResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


async def run_in_sandbox(
    *,
    workspace_root: Path,
    script_path: Path,
    output_dir: Path,
    runtime: str,
    args: list[str],
    tenant_id: str,
    task_id: str,
    timeout: int | None = None,
    max_output_bytes: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    timeout = timeout or max(1, settings.script_runner_timeout_seconds)
    max_output = max_output_bytes or max(1024, settings.script_runner_max_output_bytes)

    env = {
        **os.environ,
        "BIO_WORKSPACE": str(workspace_root),
        "BIO_OUTPUT_DIR": str(output_dir),
        "BIO_TENANT_ID": tenant_id,
        "BIO_TASK_ID": task_id,
    }

    if runtime == "python":
        local_command = ["python", str(script_path), *args]
    else:
        local_command = ["bash", str(script_path), *args]

    mode = settings.script_runner_mode.lower()

    def _execute() -> SandboxResult:
        if mode == "docker":
            return execute_docker(
                workspace_root=workspace_root,
                output_dir=output_dir,
                script_path=script_path,
                runtime=runtime,
                args=args,
                env=env,
                timeout=timeout,
            )
        return execute_local(command=local_command, cwd=workspace_root, env=env, timeout=timeout)

    try:
        result = await asyncio.to_thread(_execute)
    except subprocess.TimeoutExpired as exc:
        raise ToolExecutionError(
            "SCRIPT_TIMEOUT",
            f"Script timed out after {timeout}s",
            retryable=True,
        ) from exc

    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_preview": _truncate(result.stdout, max_output),
        "stderr_preview": _truncate(result.stderr, max_output),
        "sandbox_mode": mode,
    }

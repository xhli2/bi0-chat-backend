#!/usr/bin/env python3
"""Poll-based E2E smoke tests against a running API + Celery worker.

Usage:
  1. Start Redis/Postgres, API and worker with `.env` loaded.
  2. uv run python scripts/e2e_poll_all.py

Environment (from .env):
  E2E_BASE_URL, E2E_TENANT_ID, DEFAULT_* provider settings on server side.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TENANT_ID = os.getenv("E2E_TENANT_ID", "lab-a")
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "2"))
POLL_TIMEOUT = float(os.getenv("E2E_POLL_TIMEOUT", "180"))
APPROVAL_MAX_ROUNDS = int(os.getenv("E2E_APPROVAL_MAX_ROUNDS", "8"))
DEFAULT_APPROVE_TOOLS = [
    item.strip()
    for item in os.getenv(
        "E2E_APPROVE_TOOLS",
        "http_search_wrapper,mcp_proxy_call,bio_script_runner,bio_spliceai_submit,bio_spliceai_get_result",
    ).split(",")
    if item.strip()
]
PROVIDER_API_KEY = os.getenv("E2E_PROVIDER_API_KEY") or os.getenv("DEFAULT_PROVIDER_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
TERMINAL = {"success", "failed", "cancelled"}


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str
    task_id: str | None = None
    status: str | None = None
    event_types: set[str] | None = None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID": TENANT_ID,
        "X-Trace-ID": str(uuid.uuid4()),
    }


def _login(client: httpx.Client) -> str:
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    password = "password123"
    r = client.post(f"{BASE_URL}/api/v1/auth/register", json={"email": email, "password": password})
    if r.status_code not in {200, 409}:
        r.raise_for_status()
    r = client.post(f"{BASE_URL}/api/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def _resolve_status(state: dict[str, Any]) -> str:
    if state.get("awaiting_approval"):
        return "awaiting_approval"
    return str(state.get("status", "unknown"))


def _poll_task(
    client: httpx.Client,
    token: str,
    task_id: str,
    *,
    deadline: float | None = None,
) -> tuple[str, dict[str, Any]]:
    end = deadline if deadline is not None else time.time() + POLL_TIMEOUT
    last_state: dict[str, Any] = {}
    while time.time() < end:
        r = client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=_headers(token))
        r.raise_for_status()
        last_state = r.json()
        status = _resolve_status(last_state)
        if status in TERMINAL:
            return status, last_state
        if status == "awaiting_approval":
            return status, last_state
        time.sleep(POLL_INTERVAL)
    return "timeout", last_state


def _approval_tool_from_events(
    events: list[tuple[str, dict]],
    approved: set[str],
    fallback: list[str],
) -> str | None:
    for event_type, payload in reversed(events):
        if event_type != "approval_required":
            continue
        tool_name = payload.get("tool_name")
        if isinstance(tool_name, str) and tool_name and tool_name not in approved:
            return tool_name
    for tool_name in fallback:
        if tool_name not in approved:
            return tool_name
    return None


def _refresh_sse_events(
    client: httpx.Client,
    token: str,
    stream_url: str,
    events: list[tuple[str, dict]],
    event_types: set[str],
) -> None:
    _tail_sse_events(client, token, stream_url, events, event_types)


def _resume_payload(approved_tool: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if approved_tool:
        payload["approved_tool"] = approved_tool
    if PROVIDER_API_KEY:
        payload["provider_api_key"] = PROVIDER_API_KEY
    return payload


def _preapprove_tools(
    client: httpx.Client,
    token: str,
    task_id: str,
    tools: list[str],
    approved: set[str],
) -> None:
    for tool_name in tools:
        if tool_name in approved:
            continue
        response = client.post(
            f"{BASE_URL}/api/v1/collaboration/tasks/{task_id}/approve-tool",
            json={"tool_name": tool_name},
            headers=_headers(token),
        )
        if response.status_code == 200:
            approved.add(tool_name)


def _handle_approvals(
    client: httpx.Client,
    token: str,
    task_id: str,
    stream_url: str,
    events: list[tuple[str, dict]],
    event_types: set[str],
    approve_tools: list[str],
    deadline: float,
    *,
    initial_status: str,
    initial_state: dict[str, Any],
    approved: set[str],
) -> tuple[str, dict[str, Any], bool]:
    fallback = approve_tools or DEFAULT_APPROVE_TOOLS
    status = initial_status
    state = initial_state

    if status != "awaiting_approval" or time.time() >= deadline:
        return status, state, False

    _refresh_sse_events(client, token, stream_url, events, event_types)
    pending_tool = _approval_tool_from_events(events, approved, fallback)
    tools_to_approve = [pending_tool] if pending_tool else []
    for tool_name in fallback:
        if tool_name not in tools_to_approve and tool_name not in approved:
            tools_to_approve.append(tool_name)

    if not tools_to_approve:
        return status, state, False

    _preapprove_tools(client, token, task_id, tools_to_approve, approved)

    resume = client.post(
        f"{BASE_URL}/api/v1/agents/{task_id}/resume",
        json=_resume_payload(tools_to_approve[0]),
        headers=_headers(token),
    )
    if resume.status_code != 200:
        detail = resume.text[:200]
        raise RuntimeError(f"resume failed {resume.status_code}: {detail}")

    status, state = _poll_task(client, token, task_id, deadline=deadline)
    return status, state, True


def _collect_sse_events(
    client: httpx.Client,
    token: str,
    stream_path: str,
    max_events: int = 50,
    *,
    read_timeout: float = 20.0,
) -> list[tuple[str, dict]]:
    url = stream_path if stream_path.startswith("http") else f"{BASE_URL}{stream_path}"
    events: list[tuple[str, dict]] = []
    event_type = "message"
    timeout = httpx.Timeout(30.0, read=read_timeout)
    with client.stream("GET", url, headers=_headers(token), timeout=timeout) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
                events.append((event_type, payload))
                status = payload.get("status")
                if event_type == "status" and status in TERMINAL:
                    break
                if len(events) >= max_events:
                    break
    return events


def _tail_sse_events(
    client: httpx.Client,
    token: str,
    stream_path: str,
    events: list[tuple[str, dict]],
    event_types: set[str],
    *,
    max_events: int = 40,
) -> None:
    try:
        more = _collect_sse_events(client, token, stream_path, max_events=max_events, read_timeout=8.0)
        events.extend(more)
        event_types.update(event_type for event_type, _ in more)
    except Exception:  # noqa: BLE001
        pass


def _run_case(
    client: httpx.Client,
    token: str,
    name: str,
    payload: dict[str, Any],
    *,
    approve_tools: list[str] | None = None,
) -> CaseResult:
    r = client.post(f"{BASE_URL}/api/v1/agents/run", json=payload, headers=_headers(token))
    if r.status_code != 200:
        return CaseResult(name=name, ok=False, detail=f"run failed {r.status_code}: {r.text[:300]}")

    body = r.json()
    task_id = body["task_id"]
    stream_url = body.get("stream_url", "")
    deadline = time.time() + POLL_TIMEOUT
    events: list[tuple[str, dict]] = []
    event_types: set[str] = set()
    sse_err = ""
    approved_tools: set[str] = set()
    approval_rounds = 0

    status, state = _poll_task(client, token, task_id, deadline=deadline)

    while time.time() < deadline:
        if status == "awaiting_approval" and approval_rounds < APPROVAL_MAX_ROUNDS:
            _tail_sse_events(client, token, stream_url, events, event_types)
            tools = approve_tools if approve_tools is not None else DEFAULT_APPROVE_TOOLS
            next_status, next_state, progress = _handle_approvals(
                client,
                token,
                task_id,
                stream_url,
                events,
                event_types,
                tools,
                deadline,
                initial_status=status,
                initial_state=state,
                approved=approved_tools,
            )
            approval_rounds += 1
            if next_status == "awaiting_approval" and not progress:
                status, state = next_status, next_state
                break
            status, state = next_status, next_state
            continue
            if next_status == "awaiting_approval" and not progress:
                status, state = next_status, next_state
                break
            status, state = next_status, next_state
            continue
        if status in TERMINAL:
            break
        status, state = _poll_task(client, token, task_id, deadline=deadline)

    _tail_sse_events(client, token, stream_url, events, event_types)

    ok = status == "success"
    detail_parts = [f"status={status}"]
    if state.get("failure_reason"):
        detail_parts.append(f"reason={state['failure_reason'][:200]}")
    if approved_tools:
        detail_parts.append(f"approved={sorted(approved_tools)}")
    if sse_err:
        detail_parts.append(f"sse_error={sse_err[:120]}")
    detail_parts.append(f"events={sorted(event_types)}")

    return CaseResult(
        name=name,
        ok=ok,
        detail="; ".join(detail_parts),
        task_id=task_id,
        status=status,
        event_types=event_types,
    )


def main() -> int:
    cases = [
        (
            "skills_list",
            None,
        ),
        (
            "research_literature",
            {
                "agent_type": "research",
                "prompt": "Search PubMed literature for BRCA1 pathogenic variants. Reply in 2 sentences.",
                "model": "deepseek-v4-flash",
            },
        ),
        (
            "research_protein",
            {
                "agent_type": "research",
                "prompt": "Find UniProt accession details for TP53 protein. Brief answer.",
                "model": "deepseek-v4-flash",
            },
        ),
        (
            "supervisor_variant_splice",
            {
                "agent_type": "supervisor",
                "prompt": "Gather NCBI evidence for BRCA1 variant.\nAssess splice impact with SpliceAI.",
                "model": "deepseek-v4-flash",
            },
        ),
        (
            "supervisor_vcf_qc",
            {
                "agent_type": "supervisor",
                "prompt": "Run VCF quality control guidance for sample.vcf using workspace script.",
                "model": "deepseek-v4-flash",
            },
        ),
    ]

    print(f"E2E poll against {BASE_URL} tenant={TENANT_ID}")
    results: list[CaseResult] = []

    with httpx.Client(timeout=30.0) as client:
        try:
            health = client.get(f"{BASE_URL}/healthz")
            if health.status_code != 200:
                print(f"WARN: health check {health.status_code}")
        except httpx.HTTPError as exc:
            print(f"ERROR: cannot reach API at {BASE_URL}: {exc}")
            print("Start: uv run uvicorn app.main:app --reload")
            print("Worker: uv run celery -A app.worker.celery_app.celery_app worker -Q high,default,low")
            return 1

        token = _login(client)

        r = client.get(f"{BASE_URL}/api/v1/agents/skill-specs", headers=_headers(token))
        if r.status_code == 200:
            specs = r.json()
            results.append(
                CaseResult(
                    name="skills_list",
                    ok=len(specs) >= 7,
                    detail=f"skill_count={len(specs)}",
                )
            )
        else:
            results.append(CaseResult(name="skills_list", ok=False, detail=f"HTTP {r.status_code}"))

        for item in cases[1:]:
            name = item[0]
            payload = item[1]
            approve = item[2] if len(item) > 2 else None
            print(f"Running {name} ...")
            result = _run_case(client, token, name, payload, approve_tools=approve)
            results.append(result)
            mark = "PASS" if result.ok else "FAIL"
            print(f"  [{mark}] {result.detail}")

    passed = sum(1 for r in results if r.ok)
    total = len(results)
    print(f"\nSummary: {passed}/{total} passed")
    for r in results:
        mark = "OK" if r.ok else "XX"
        print(f"  [{mark}] {r.name}: {r.detail}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

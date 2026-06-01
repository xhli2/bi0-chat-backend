#!/usr/bin/env python3
"""Multi-turn session demo: run sample queries, exercise session APIs, write flow report.

Usage:
  docker compose up -d
  uv run alembic upgrade head
  uv run python scripts/demo_session_flow.py

Output:
  reports/demo-session-flow-report.md
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TENANT_ID = os.getenv("E2E_TENANT_ID", "public")
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "2"))
POLL_TIMEOUT = float(os.getenv("DEMO_POLL_TIMEOUT", os.getenv("E2E_POLL_TIMEOUT", "240")))
MODEL = os.getenv("DEMO_MODEL", os.getenv("DEFAULT_LLM_MODEL", "builtin") or "builtin")
PROVIDER_API_KEY = (
    os.getenv("DEMO_PROVIDER_API_KEY")
    or os.getenv("E2E_PROVIDER_API_KEY")
    or os.getenv("DEFAULT_PROVIDER_API_KEY")
    or ""
)
APPROVE_TOOLS = [
    item.strip()
    for item in os.getenv(
        "E2E_APPROVE_TOOLS",
        "http_search_wrapper,mcp_proxy_call,bio_script_runner,bio_spliceai_submit,bio_spliceai_get_result,"
        "bio_mygene_query,bio_ensembl_gene_lookup,bio_ensembl_vep,bio_pdb_search,bio_alphafold_lookup,"
        "bio_ncbi_search,bio_uniprot_lookup",
    ).split(",")
    if item.strip()
]
TERMINAL = {"success", "failed", "cancelled"}


@dataclass
class DemoQuery:
    id: str
    title: str
    agent_type: str
    prompt: str
    model: str = MODEL
    note: str = ""


@dataclass
class StepLog:
    query_id: str
    title: str
    task_id: str | None = None
    status: str | None = None
    session_id: str | None = None
    kv_hit: bool | None = None
    summary_hit: bool | None = None
    event_types: list[str] = field(default_factory=list)
    api_checks: dict[str, Any] = field(default_factory=dict)
    tools_observed: list[str] = field(default_factory=list)
    tools_expected: list[str] = field(default_factory=list)
    tools_check_ok: bool | None = None
    error: str | None = None


DEMO_QUERIES: list[DemoQuery] = [
    DemoQuery(
        id="Q1",
        title="会话偏好 + 变异背景（写入 KV 友好格式）",
        agent_type="research",
        prompt=(
            "genome_build: GRCh38\n"
            "report_language: 中文\n\n"
            "请用 2-3 句话介绍剪接位点变异 NM_000518.5:c.694+1G>A 的一般分析思路。"
        ),
        note="消息含 key:value，refresh 后应进入 chat_memory_kv；拼 prompt 时 kv_hit 可能仍为 false（需 summarize/8轮阈值）。",
    ),
    DemoQuery(
        id="Q2",
        title="文献检索（NCBI 工具链）",
        agent_type="research",
        prompt="Search PubMed for BRCA1 c.694+1G>A splicing. Reply in 2 sentences, 中文.",
        note="预期 chat_tool_calls 出现 bio_ncbi_search；需工具审批时 pre-approve。",
    ),
    DemoQuery(
        id="Q3",
        title="Supervisor 提交 SpliceAI",
        agent_type="supervisor",
        prompt=(
            "对变异 NM_000518.5:c.694+1G>A（GRCh38）执行 SpliceAI 评估。\n"
            "先 submit job，再简要说明 job_id。"
        ),
        note="预期 session_entities(job+variant)、spliceai_jobs、session_runs.plan_json。",
    ),
    DemoQuery(
        id="Q4",
        title="续聊：结果是否出来（跨轮 memory）",
        agent_type="research",
        prompt="SpliceAI 结果出来了吗？若已有 job，请 get_result 并中文总结，不要重复 submit。",
        note="Prompt 应含 [ActiveAnalysis]、[RecentToolCalls]；agent 应读取 job 状态。",
    ),
    DemoQuery(
        id="Q5",
        title="基因注释（MyGene + Ensembl）",
        agent_type="research",
        prompt=(
            "请用 gene annotation 流程查 BRCA1：MyGene 解析 symbol/Entrez，"
            "再用 Ensembl 确认染色体坐标和 biotype。中文 2 句话总结。"
        ),
        note="skill=gene-annotation；预期 bio_mygene_query + bio_ensembl_gene_lookup。",
    ),
    DemoQuery(
        id="Q6",
        title="变异后果（Ensembl VEP）",
        agent_type="research",
        prompt=(
            "用 Ensembl VEP 注释变异 9:g.22125504G>C，"
            "说明 most_severe_consequence 和影响基因。中文简要回答。"
        ),
        note="skill=variant-consequence；预期 bio_ensembl_vep。",
    ),
    DemoQuery(
        id="Q7",
        title="蛋白结构（PDB + AlphaFold）",
        agent_type="research",
        prompt=(
            "查 BRCA1 相关 PDB 实验结构（top 3）以及 UniProt P38398 的 AlphaFold 预测模型 ID。"
            "中文列表回答。"
        ),
        note="skill=structure-lookup；预期 bio_pdb_search + bio_alphafold_lookup。",
    ),
    DemoQuery(
        id="Q8",
        title="ClinVar 临床意义检索",
        agent_type="research",
        prompt=(
            "在 ClinVar 里搜 BRCA1 pathogenic 相关记录，用 db=clinvar，"
            "返回 top ID 并中文说明需进一步读 record。"
        ),
        note="skill=clinvar-lookup；预期 bio_ncbi_search(db=clinvar)。",
    ),
    DemoQuery(
        id="Q9",
        title="序列统计（sequence-utils）",
        agent_type="research",
        prompt=(
            "请说明如何用 sequence-utils 的 seq_stats.py 统计 FASTA 的 GC 含量；"
            "若 workspace 已有 inputs/seq.fasta 则运行 bio_script_runner 并报告结果。"
        ),
        note="skill=sequence-utils；builtin 可能不调工具，见 bio_tool_smoke。",
    ),
]

EXPECTED_TOOLS: dict[str, list[str]] = {
    "Q2": ["bio_ncbi_search"],
    "Q3": ["bio_spliceai_submit"],
    "Q4": ["bio_spliceai_get_result"],
    "Q5": ["bio_mygene_query", "bio_ensembl_gene_lookup"],
    "Q6": ["bio_ensembl_vep"],
    "Q7": ["bio_pdb_search", "bio_alphafold_lookup"],
    "Q8": ["bio_ncbi_search"],
    "Q9": ["bio_script_runner"],
}


def _headers(token: str, trace_id: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID": TENANT_ID,
        "X-Trace-ID": trace_id or str(uuid.uuid4()),
    }


def _timeline_tool_names(timeline_body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in timeline_body.get("tool_calls", []):
        if isinstance(item, dict) and isinstance(item.get("tool_name"), str):
            names.append(item["tool_name"])
    return names


async def _run_bio_tool_smoke() -> dict[str, Any]:
    """Direct-call new bio tools (no LLM) to validate adapters + upstream APIs."""
    from app.tools.adapters.bio_extended import (
        tool_bio_alphafold_lookup,
        tool_bio_ensembl_gene_lookup,
        tool_bio_ensembl_vep,
        tool_bio_mygene_query,
        tool_bio_pdb_search,
    )
    from app.tools.adapters.bio_public import tool_bio_ncbi_search

    results: dict[str, Any] = {}

    async def _call(name: str, coro) -> None:
        try:
            payload = await coro
            results[name] = {"ok": True, "preview": str(payload)[:200]}
        except Exception as exc:  # noqa: BLE001
            results[name] = {"ok": False, "error": str(exc)[:300]}

    await _call("bio_mygene_query", tool_bio_mygene_query({"query": "symbol:BRCA1", "size": 1}, {}))
    await _call("bio_ensembl_gene_lookup", tool_bio_ensembl_gene_lookup({"symbol": "BRCA1"}, {}))
    await _call("bio_ensembl_vep", tool_bio_ensembl_vep({"variant_hgvs": "9:g.22125504G>C"}, {}))
    await _call("bio_pdb_search", tool_bio_pdb_search({"query": "BRCA1", "rows": 3}, {}))
    await _call("bio_alphafold_lookup", tool_bio_alphafold_lookup({"uniprot_accession": "P38398"}, {}))
    await _call(
        "bio_ncbi_search_clinvar",
        tool_bio_ncbi_search({"term": "BRCA1 pathogenic", "db": "clinvar", "retmax": 3}, {}),
    )

    seq_script = ROOT / "skills" / "sequence-utils" / "scripts" / "seq_stats.py"
    if seq_script.exists():
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inputs = tmp_path / "inputs"
            inputs.mkdir()
            (inputs / "seq.fasta").write_text(">demo\nATGCATGC\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(seq_script)],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            results["seq_stats.py"] = {
                "ok": proc.returncode == 0,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip()[:200],
            }

    results["summary"] = {
        "passed": sum(1 for item in results.values() if isinstance(item, dict) and item.get("ok")),
        "total": sum(1 for item in results.values() if isinstance(item, dict) and "ok" in item),
    }
    return results


def _run_bio_tool_smoke_sync() -> dict[str, Any]:
    import asyncio

    return asyncio.run(_run_bio_tool_smoke())


def _json_preview(data: Any, limit: int = 1200) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _login(client: httpx.Client) -> str:
    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    password = "password123"
    client.post(f"{BASE_URL}/api/v1/auth/register", json={"email": email, "password": password})
    response = client.post(f"{BASE_URL}/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()["access_token"]


def _resolve_status(state: dict[str, Any]) -> str:
    if state.get("awaiting_approval"):
        return "awaiting_approval"
    return str(state.get("status", "unknown"))


def _poll_task(client: httpx.Client, token: str, task_id: str, deadline: float) -> tuple[str, dict[str, Any]]:
    last: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=_headers(token))
        response.raise_for_status()
        last = response.json()
        status = _resolve_status(last)
        if status in TERMINAL or status == "awaiting_approval":
            return status, last
        time.sleep(POLL_INTERVAL)
    return "timeout", last


def _preapprove_all(client: httpx.Client, token: str, task_id: str) -> list[str]:
    approved: list[str] = []
    for tool_name in APPROVE_TOOLS:
        response = client.post(
            f"{BASE_URL}/api/v1/collaboration/tasks/{task_id}/approve-tool",
            json={"tool_name": tool_name},
            headers=_headers(token),
        )
        if response.status_code == 200:
            approved.append(tool_name)
    return approved


def _run_agent_with_approval(
    client: httpx.Client,
    token: str,
    session_id: str,
    query: DemoQuery,
) -> tuple[str | None, str, dict[str, Any], list[str], dict[str, Any]]:
    payload: dict[str, Any] = {
        "agent_type": query.agent_type,
        "prompt": query.prompt,
        "model": query.model,
        "session_id": session_id,
        "context_policy": "balanced",
    }
    if PROVIDER_API_KEY and query.model != "builtin":
        payload["provider_api_key"] = PROVIDER_API_KEY

    response = client.post(f"{BASE_URL}/api/v1/agents/run", json=payload, headers=_headers(token))
    response.raise_for_status()
    body = response.json()
    task_id = body["task_id"]
    deadline = time.time() + POLL_TIMEOUT
    status, state = _poll_task(client, token, task_id, deadline)
    event_types: list[str] = []
    context_meta: dict[str, Any] = {}

    if status == "awaiting_approval":
        _preapprove_all(client, token, task_id)
        resume_payload: dict[str, Any] = {}
        if APPROVE_TOOLS:
            resume_payload["approved_tool"] = APPROVE_TOOLS[0]
        if PROVIDER_API_KEY:
            resume_payload["provider_api_key"] = PROVIDER_API_KEY
        resume = client.post(
            f"{BASE_URL}/api/v1/agents/{task_id}/resume",
            json=resume_payload,
            headers=_headers(token),
        )
        resume.raise_for_status()
        status, state = _poll_task(client, token, task_id, deadline=deadline)

    try:
        stream_path = body.get("stream_url", f"/api/v1/tasks/{task_id}/stream")
        url = stream_path if str(stream_path).startswith("http") else f"{BASE_URL}{stream_path}"
        with client.stream("GET", url, headers=_headers(token), timeout=httpx.Timeout(30.0, read=10.0)) as stream:
            stream.raise_for_status()
            event_type = "message"
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    event_types.append(event_type)
                elif line.startswith("data:"):
                    payload_data = json.loads(line.split(":", 1)[1].strip())
                    if event_type == "status" and payload_data.get("message") == "Context prepared":
                        context_meta = payload_data
                    if event_type == "status" and payload_data.get("status") in TERMINAL:
                        break
                if len(event_types) > 80:
                    break
    except Exception:  # noqa: BLE001
        pass

    return task_id, status, state, sorted(set(event_types)), context_meta


def _fetch_session_snapshot(client: httpx.Client, token: str, session_id: str) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    endpoints = {
        "session": f"/api/v1/sessions/{session_id}",
        "messages": f"/api/v1/sessions/{session_id}/messages?size=20",
        "memory": f"/api/v1/sessions/{session_id}/memory",
        "summary": f"/api/v1/sessions/{session_id}/summary",
        "runs": f"/api/v1/sessions/{session_id}/runs",
        "timeline": f"/api/v1/sessions/{session_id}/timeline",
        "artifacts": f"/api/v1/sessions/{session_id}/artifacts",
        "token_usage": f"/api/v1/sessions/{session_id}/token-usage",
        "diagnostics": f"/api/v1/sessions/{session_id}/diagnostics",
    }
    for key, path in endpoints.items():
        response = client.get(f"{BASE_URL}{path}", headers=_headers(token))
        snapshot[key] = {"status_code": response.status_code, "body": response.json() if response.status_code == 200 else response.text[:300]}
    return snapshot


def _write_report(
    *,
    started_at: str,
    finished_at: str,
    session_id: str,
    steps: list[StepLog],
    preflight: dict[str, Any],
    bio_tool_smoke: dict[str, Any] | None,
    post_summarize: dict[str, Any] | None,
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "demo-session-flow-report.md"
    lines: list[str] = [
        "# Demo Session Flow 测试报告",
        "",
        f"- 生成时间: {finished_at}",
        f"- API: `{BASE_URL}`",
        f"- Tenant: `{TENANT_ID}`",
        f"- Session ID: `{session_id}`",
        f"- Model: `{MODEL}`",
        "",
        "## 1. 预检",
        "",
        "```json",
        _json_preview(preflight, 2000),
        "```",
        "",
    ]
    if bio_tool_smoke:
        lines.extend([
            "## 2. Bio 工具直连 Smoke（MyGene/Ensembl/VEP/PDB/AlphaFold/ClinVar/seq_stats）",
            "",
            "```json",
            _json_preview(bio_tool_smoke, 3500),
            "```",
            "",
        ])
    lines.extend([
        "## 3. Demo Query 定义",
        "",
        "| ID | 标题 | agent_type | 说明 |",
        "|----|------|------------|------|",
    ])
    for query in DEMO_QUERIES:
        lines.append(f"| {query.id} | {query.title} | `{query.agent_type}` | {query.note} |")

    lines.extend(["", "## 4. 逐步执行结果", ""])
    for step in steps:
        lines.append(f"### {step.query_id} — {step.title}")
        lines.append("")
        lines.append(f"- task_id: `{step.task_id}`")
        lines.append(f"- status: `{step.status}`")
        if step.tools_expected:
            lines.append(f"- expected_tools: `{', '.join(step.tools_expected)}`")
            lines.append(f"- observed_tools: `{', '.join(step.tools_observed) or '(none)'}`")
            if step.tools_check_ok is not None:
                lines.append(f"- tools_check: `{'OK' if step.tools_check_ok else 'SKIP/FAIL'}`")
        if step.kv_hit is not None:
            lines.append(f"- kv_hit: `{step.kv_hit}`")
        if step.summary_hit is not None:
            lines.append(f"- summary_hit: `{step.summary_hit}`")
        if step.event_types:
            lines.append(f"- SSE events: `{', '.join(step.event_types[:20])}`")
        if step.error:
            lines.append(f"- **error**: {step.error}")
        lines.append("")
        lines.append("**API 快照（摘要）**")
        lines.append("")
        lines.append("```json")
        lines.append(_json_preview(step.api_checks, 2500))
        lines.append("```")
        lines.append("")

    if post_summarize:
        lines.extend([
            "## 5. 手动 Summarize 后 Memory/KV",
            "",
            "```json",
            _json_preview(post_summarize, 2500),
            "```",
            "",
        ])

    lines.extend([
        "## 6. 端到端数据流（参考）",
        "",
        "```mermaid",
        "sequenceDiagram",
        "  participant U as User/API Client",
        "  participant S as Sessions API",
        "  participant A as Agents/Run",
        "  participant W as Celery Worker",
        "  participant PG as PostgreSQL",
        "  participant R as Redis",
        "",
        "  U->>S: POST /sessions",
        "  U->>A: POST /agents/run (session_id, prompt)",
        "  A->>PG: user message + session_runs(running)",
        "  A->>R: task state + run_spec",
        "  W->>PG: ContextLoader 读 messages/summary/kv/entities",
        "  W->>W: PromptBuilder → kv_hit/summary_hit",
        "  W->>PG: tool_calls + entities + assistant message",
        "  W->>PG: session_runs(complete)",
        "  U->>S: GET /timeline /memory /token-usage",
        "```",
        "",
        "## 7. 验收清单",
        "",
        "| 检查项 | 预期 |",
        "|--------|------|",
        "| `GET /sessions/{id}/messages` | 每轮 user+assistant |",
        "| `GET /sessions/{id}/runs` | 每轮 task 一条，含 usage |",
        "| `GET /sessions/{id}/timeline` | entities/tool_calls 在 Q3/Q4 后出现 |",
        "| `POST /sessions/{id}/summarize` 后 `/memory` | 含 genome_build 等 KV |",
        "| SSE `Context prepared` | 可观测 kv_hit / summary_hit |",
        "| Bio 工具 smoke | MyGene/Ensembl/VEP/PDB/AlphaFold/ClinVar/seq_stats 直连 OK |",
        "| Q5–Q9 timeline | 真实 LLM 时应出现对应 tool_calls；builtin 见 smoke |",
        "",
        "## 8. 相关 API 索引",
        "",
        "| 方法 | 路径 | 用途 |",
        "|------|------|------|",
        "| POST | `/api/v1/auth/register` `/login` | 鉴权 |",
        "| POST | `/api/v1/sessions` | 创建会话 |",
        "| GET | `/api/v1/sessions` | 列表 |",
        "| POST | `/api/v1/agents/run` | 发起 agent |",
        "| POST | `/api/v1/agents/{task_id}/resume` | 审批后继续 |",
        "| POST | `/api/v1/collaboration/tasks/{task_id}/approve-tool` | 预批工具 |",
        "| GET | `/api/v1/sessions/{id}/timeline` | 全量时间线 |",
        "| GET | `/api/v1/sessions/{id}/token-usage` | Token 统计 |",
        "| POST | `/api/v1/sessions/{id}/summarize` | 触发 summary+KV refresh |",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    steps: list[StepLog] = []
    preflight: dict[str, Any] = {}
    bio_tool_smoke: dict[str, Any] | None = None
    post_summarize: dict[str, Any] | None = None

    print(f"Demo session flow → {BASE_URL} tenant={TENANT_ID} model={MODEL}")

    print("Running bio tool direct smoke ...")
    try:
        bio_tool_smoke = _run_bio_tool_smoke_sync()
        smoke_ok = bio_tool_smoke.get("summary", {}).get("passed", 0)
        smoke_total = bio_tool_smoke.get("summary", {}).get("total", 0)
        print(f"  Bio smoke: {smoke_ok}/{smoke_total} passed")
    except Exception as exc:  # noqa: BLE001
        bio_tool_smoke = {"error": str(exc)}
        print(f"  Bio smoke FAILED: {exc}")

    with httpx.Client(timeout=60.0) as client:
        try:
            preflight["healthz"] = client.get(f"{BASE_URL}/healthz").json()
            preflight["readyz"] = client.get(f"{BASE_URL}/readyz").json()
        except httpx.HTTPError as exc:
            print(f"ERROR: API unreachable: {exc}")
            preflight["api_error"] = str(exc)
            finished_at = datetime.now(timezone.utc).isoformat()
            report_path = _write_report(
                started_at=started_at,
                finished_at=finished_at,
                session_id="(api-unreachable)",
                steps=steps,
                preflight=preflight,
                bio_tool_smoke=bio_tool_smoke,
                post_summarize=None,
            )
            print(f"\nPartial report (bio smoke only): {report_path}")
            smoke_passed = (
                bio_tool_smoke.get("summary", {}).get("passed", 0) == bio_tool_smoke.get("summary", {}).get("total", 0)
                if isinstance(bio_tool_smoke, dict) and "summary" in bio_tool_smoke
                else False
            )
            return 0 if smoke_passed else 1

        token = _login(client)
        session_response = client.post(
            f"{BASE_URL}/api/v1/sessions",
            json={"title": "Demo Bio Session"},
            headers=_headers(token),
        )
        session_response.raise_for_status()
        session_id = session_response.json()["id"]
        print(f"Session: {session_id}")

        for query in DEMO_QUERIES:
            print(f"Running {query.id}: {query.title} ...")
            log = StepLog(query_id=query.id, title=query.title, session_id=session_id)
            log.tools_expected = EXPECTED_TOOLS.get(query.id, [])
            try:
                task_id, status, _state, events, context_meta = _run_agent_with_approval(
                    client, token, session_id, query
                )
                log.task_id = task_id
                log.status = status
                log.event_types = events
                log.kv_hit = context_meta.get("kv_hit")
                log.summary_hit = context_meta.get("summary_hit")
                if status != "success":
                    log.error = f"terminal status={status}"
                time.sleep(1)
                log.api_checks = _fetch_session_snapshot(client, token, session_id)
                timeline_body = log.api_checks.get("timeline", {}).get("body", {})
                if isinstance(timeline_body, dict):
                    log.tools_observed = _timeline_tool_names(timeline_body)
                if log.tools_expected and MODEL != "builtin":
                    log.tools_check_ok = all(tool in log.tools_observed for tool in log.tools_expected)
                    if not log.tools_check_ok and not log.error:
                        log.error = f"missing tools: {log.tools_expected} got {log.tools_observed}"
                elif log.tools_expected:
                    log.tools_check_ok = None
            except Exception as exc:  # noqa: BLE001
                log.error = str(exc)
            steps.append(log)
            mark = "OK" if log.status == "success" and not log.error else "FAIL"
            print(f"  [{mark}] status={log.status} kv_hit={log.kv_hit}")

        print("Trigger summarize for KV extraction ...")
        summarize = client.post(
            f"{BASE_URL}/api/v1/sessions/{session_id}/summarize",
            headers=_headers(token),
        )
        time.sleep(5)
        post_summarize = {
            "summarize_status": summarize.status_code,
            "memory": client.get(f"{BASE_URL}/api/v1/sessions/{session_id}/memory", headers=_headers(token)).json(),
            "summary": client.get(f"{BASE_URL}/api/v1/sessions/{session_id}/summary", headers=_headers(token)).json(),
            "user_token_usage": client.get(f"{BASE_URL}/api/v1/sessions/token-usage", headers=_headers(token)).json(),
        }

    finished_at = datetime.now(timezone.utc).isoformat()
    report_path = _write_report(
        started_at=started_at,
        finished_at=finished_at,
        session_id=session_id,
        steps=steps,
        preflight=preflight,
        bio_tool_smoke=bio_tool_smoke,
        post_summarize=post_summarize,
    )
    passed = sum(1 for step in steps if step.status == "success" and not step.error)
    smoke_passed = (
        bio_tool_smoke.get("summary", {}).get("passed", 0) == bio_tool_smoke.get("summary", {}).get("total", 0)
        if isinstance(bio_tool_smoke, dict) and "summary" in bio_tool_smoke
        else False
    )
    print(f"\nReport: {report_path}")
    print(f"Agent steps: {passed}/{len(steps)} success")
    print(f"Bio tool smoke: {'OK' if smoke_passed else 'FAIL'}")
    return 0 if passed == len(steps) and smoke_passed else 1


if __name__ == "__main__":
    sys.exit(main())

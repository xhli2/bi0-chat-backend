import pytest

from app.agent.hook_registry import HookEvent, hook_registry
from app.agent.hooks import emit_hook, register_default_hooks, validate_hgvs
from app.agent.subagent_runner import SUBAGENT_TOOL_POLICIES, run_subagent_step
from app.services.script_workspace import ensure_workspace, script_path
from app.tools import tool_registry
from app.tools.executor import ToolExecutor
from app.tools.schemas import ToolExecutionContext, ToolExecutionError


def test_hook_registry_registers_defaults():
    register_default_hooks()
    assert hook_registry._handlers[HookEvent.PRE_TOOL_USE]
    assert hook_registry._handlers[HookEvent.PRE_SCRIPT_RUN]


def test_pre_script_hook_rejects_invalid_runtime():
    register_default_hooks()
    with pytest.raises(ToolExecutionError) as exc:
        emit_hook(
            HookEvent.PRE_SCRIPT_RUN,
            tenant_id="public",
            task_id="task-1",
            script_name="hello.py",
            tool_args={"script_name": "hello.py", "runtime": "node"},
        )
    assert exc.value.code == "SCRIPT_RUNTIME_INVALID"


@pytest.mark.asyncio
async def test_subagent_creates_child_task_and_restricts_tools(monkeypatch):
    async def _fake_ncbi(args, _):
        return {
            "db": "pubmed",
            "term": args["term"],
            "total_count": 1,
            "ids": ["1"],
            "source": "ncbi-esearch",
            "retrieved_at": "2026-05-31T00:00:00+00:00",
            "evidence": {
                "source": "ncbi",
                "entity_type": "literature",
                "identifiers": {"term": args["term"], "db": "pubmed", "count": "1", "top_id": "1"},
                "retrieved_at": "2026-05-31T00:00:00+00:00",
                "confidence": 0.9,
                "summary": "Found 1 record(s) in pubmed",
                "raw_ref": "ncbi:pubmed:test",
            },
        }

    spec = tool_registry.get("bio_ncbi_search")
    assert spec is not None
    monkeypatch.setattr(spec, "executor", _fake_ncbi)
    executor = ToolExecutor(tool_registry)
    result = await run_subagent_step(
        parent_task_id="parent-task",
        role="research_worker",
        prompt="Search PubMed for BRCA1 variants",
        trace_id="trace-1",
        tenant_id="lab-a",
        session_id="session-1",
        user_id=1,
        permissions={"bio:ncbi:read", "session:read"},
        scopes=set(),
        approved_tools=set(),
        step_tools=["bio_ncbi_search"],
        executor=executor,
    )
    assert result.child_task_id
    assert result.subagent_id
    assert "bio_ncbi_search" in (result.tools_executed or [])
    assert "bio_script_runner" not in SUBAGENT_TOOL_POLICIES["research_worker"]


@pytest.mark.asyncio
async def test_bio_script_runner_executes_workspace_script(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIPT_WORKSPACE_ROOT", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()

    workspace = ensure_workspace(tenant_id="public", session_id="sess-1", task_id="task-1")
    script_file = script_path(tenant_id="public", session_id="sess-1", task_id="task-1", script_name="hello.py")
    script_file.write_text("print('hello-bio')", encoding="utf-8")

    from app.tools.adapters.bio_script_runner import tool_bio_script_runner

    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="sess-1",
        trace_id="trace-1",
        task_id="task-1",
        permissions={"bio:script:run"},
        approved_tools={"bio_script_runner"},
    )
    output = await tool_bio_script_runner(
        {"script_name": "hello.py", "runtime": "python", "args": []},
        {"context": context},
    )
    assert output["exit_code"] == 0
    assert "hello-bio" in output["stdout_preview"]
    get_settings.cache_clear()


def test_validate_hgvs_still_works():
    validate_hgvs("NM_007294.3:c.5266dupC")

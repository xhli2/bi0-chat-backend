import pytest

from app.agent.skill_resolver import build_skill_instructions, resolve_skills
from app.services.script_workspace import ensure_workspace, script_path
from app.services.skill_environment import (
    bundled_script_sources,
    load_skill_instructions_from_disk,
    materialize_skills_to_workspace,
    read_workspace_manifest,
)


def test_bundled_scripts_exist_for_script_skills():
    for skill_name in ("splice-analysis", "vcf-qc", "report-synthesis", "variant-interpretation"):
        sources = bundled_script_sources(skill_name)
        assert sources, f"expected bundled scripts for {skill_name}"


def test_load_skill_instructions_from_disk():
    text = load_skill_instructions_from_disk("literature-triage")
    assert text
    assert "PubMed" in text


def test_build_skill_instructions_includes_disk_body():
    skills = resolve_skills(prompt="Search PubMed literature", agent_type="research")
    text = build_skill_instructions(skills)
    assert "Literature Triage" in text or "PubMed" in text


def test_materialize_skills_to_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIPT_WORKSPACE_ROOT", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()

    copied = materialize_skills_to_workspace(
        skill_names=["vcf-qc", "splice-analysis"],
        tenant_id="lab-a",
        session_id="sess-1",
        task_id="task-1",
    )
    assert "vcf_qc.py" in copied
    assert "check_hgvs.py" in copied

    script_file = script_path(
        tenant_id="lab-a",
        session_id="sess-1",
        task_id="task-1",
        script_name="vcf_qc.py",
    )
    assert script_file.exists()

    manifest = read_workspace_manifest(tenant_id="lab-a", session_id="sess-1", task_id="task-1")
    assert "vcf_qc.py" in manifest.get("scripts", {})
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_vcf_qc_script_runs_in_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIPT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("SKILL_SCRIPT_ALLOWLIST_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()

    materialize_skills_to_workspace(
        skill_names=["vcf-qc"],
        tenant_id="lab-a",
        session_id="sess-1",
        task_id="task-1",
    )
    root = ensure_workspace(tenant_id="lab-a", session_id="sess-1", task_id="task-1")
    sample_vcf = root / "inputs" / "sample.vcf"
    sample_vcf.write_text(
        "##fileformat=VCF4.2\n#CHROM\tPOS\tID\tREF\tALT\nchr1\t100\t.\tA\tG\n",
        encoding="utf-8",
    )

    from app.tools.adapters.bio_script_runner import tool_bio_script_runner
    from app.tools.schemas import ToolExecutionContext

    context = ToolExecutionContext(
        tenant_id="lab-a",
        user_id=1,
        session_id="sess-1",
        trace_id="trace-1",
        task_id="task-1",
        permissions={"bio:script:run"},
        approved_tools={"bio_script_runner"},
    )
    output = await tool_bio_script_runner(
        {"script_name": "vcf_qc.py", "runtime": "python", "args": ["inputs/sample.vcf"]},
        {"context": context},
    )
    assert output["exit_code"] == 0
    assert "PASS" in output["stdout_preview"]
    get_settings.cache_clear()


def test_resolve_new_skills():
    vcf_skills = resolve_skills(prompt="Run VCF quality control qc", agent_type="research")
    assert any(skill.name == "vcf-qc" for skill in vcf_skills)
    assert any(skill.name == "web-search" for skill in vcf_skills)
    report_skills = resolve_skills(prompt="Synthesize clinical report", agent_type="supervisor")
    assert any(skill.name == "report-synthesis" for skill in report_skills)
    assert any(skill.name == "session-recall" for skill in report_skills)


def test_default_agent_skills_without_triggers():
    skills = resolve_skills(prompt="Hello", agent_type="research")
    names = {skill.name for skill in skills}
    assert "web-search" in names
    assert "session-recall" in names


def test_load_general_skill_instructions():
    for skill_name in ("web-search", "session-recall", "mcp-bridge", "general-assistant"):
        text = load_skill_instructions_from_disk(skill_name)
        assert text
        assert len(text) > 20

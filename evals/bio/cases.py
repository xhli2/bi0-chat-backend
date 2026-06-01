from __future__ import annotations

from dataclasses import dataclass

from app.agent.planner import build_structured_plan
from app.agent.skill_resolver import merged_skill_tools, resolve_skills
from app.agent.skill_specs import DEFAULT_SKILL_SPECS


@dataclass
class EvalCase:
    name: str
    prompt: str
    agent_type: str
    expected_tools: set[str]
    expected_skills: set[str]
    min_plan_steps: int = 1


BIO_EVAL_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="literature_should_use_ncbi",
        prompt="Search PubMed literature for BRCA1 pathogenic variants",
        agent_type="research",
        expected_tools={"bio_ncbi_search"},
        expected_skills={"literature-triage"},
    ),
    EvalCase(
        name="protein_lookup_should_use_uniprot",
        prompt="Find UniProt accession details for TP53 protein",
        agent_type="research",
        expected_tools={"bio_uniprot_lookup"},
        expected_skills={"protein-lookup"},
    ),
    EvalCase(
        name="variant_plan_should_have_multiple_steps",
        prompt="Gather NCBI evidence for BRCA1 variant.\nAssess splice impact with SpliceAI.",
        agent_type="supervisor",
        expected_tools={"bio_ncbi_search", "bio_spliceai_submit"},
        expected_skills={"variant-interpretation", "splice-analysis"},
        min_plan_steps=2,
    ),
    EvalCase(
        name="vcf_qc_should_use_script_runner",
        prompt="Run VCF quality control on my sample.vcf file",
        agent_type="research",
        expected_tools={"bio_script_runner"},
        expected_skills={"vcf-qc"},
    ),
    EvalCase(
        name="report_synthesis_should_use_report_skill",
        prompt="Synthesize a clinical report from collected evidence",
        agent_type="supervisor",
        expected_tools={"bio_script_runner", "summarize_chunk"},
        expected_skills={"report-synthesis"},
    ),
)


def evaluate_tool_selection(case: EvalCase) -> dict[str, bool | set[str]]:
    skills = resolve_skills(prompt=case.prompt, agent_type=case.agent_type, max_skills=3)
    skill_names = {skill.name for skill in skills}
    tools = set(merged_skill_tools(skills))
    plan = build_structured_plan(
        prompt=case.prompt,
        available_tools=sorted(tools),
        max_steps=5,
    )
    plan_tools: set[str] = set()
    for step in plan.steps:
        plan_tools.update(step.tools)
    return {
        "skills_ok": case.expected_skills.issubset(skill_names),
        "tools_ok": case.expected_tools.issubset(tools),
        "plan_steps_ok": len(plan.steps) >= case.min_plan_steps,
        "resolved_skills": skill_names,
        "resolved_tools": tools,
        "plan_tools": plan_tools,
    }

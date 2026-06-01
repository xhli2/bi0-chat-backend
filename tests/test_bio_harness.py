import pytest

from app.agent.hooks import validate_hgvs
from app.agent.planner import build_structured_plan
from app.agent.skill_resolver import resolve_skills
from app.core.exceptions import ApiError
from app.services.context_pack_loader import load_context_pack_text, select_context_packs
from app.tools.schemas import ToolExecutionError
from evals.bio.cases import BIO_EVAL_CASES, evaluate_tool_selection


def test_validate_hgvs_accepts_common_format():
    validate_hgvs("NM_007294.3:c.5266dupC")


def test_validate_hgvs_rejects_newlines():
    with pytest.raises(ToolExecutionError) as exc:
        validate_hgvs("NM_007294.3:c.5266\nhack")
    assert exc.value.code == "HGVS_INVALID"


def test_context_pack_loader_selects_variant_domain():
    matches = select_context_packs(
        prompt="Interpret HGVS variant pathogenicity",
        agent_type="research",
        tenant_id="public",
    )
    pack_ids = [match.pack_id for match in matches]
    assert "global/base" in pack_ids
    assert "domains/variant-analysis" in pack_ids


def test_context_pack_loader_injects_into_prompt_builder_text():
    text, pack_ids = load_context_pack_text(
        prompt="UniProt protein lookup",
        agent_type="research",
        tenant_id="public",
    )
    assert pack_ids
    assert "ContextPack:" in text


def test_skill_resolver_variant_interpretation():
    skills = resolve_skills(prompt="Assess HGVS variant pathogenicity", agent_type="research")
    assert any(skill.name == "variant-interpretation" for skill in skills)


def test_planner_assigns_agent_roles():
    plan = build_structured_plan(
        prompt="Gather NCBI evidence\nAssess splice impact with SpliceAI",
        available_tools=["bio_ncbi_search", "bio_spliceai_submit"],
        max_steps=2,
    )
    assert len(plan.steps) == 2
    assert plan.steps[0].agent_role == "research_worker"
    assert plan.steps[1].agent_role == "analysis_worker"


@pytest.mark.parametrize("case", BIO_EVAL_CASES, ids=[case.name for case in BIO_EVAL_CASES])
def test_bio_eval_cases(case):
    outcome = evaluate_tool_selection(case)
    assert outcome["skills_ok"]
    assert outcome["tools_ok"]
    assert outcome["plan_steps_ok"]

from app.agent.planner import PlanStep, StructuredPlan, load_plan_from_checkpoints


def test_load_plan_from_checkpoints_restores_latest_plan():
    checkpoints = [
        {
            "kind": "plan_created",
            "plan_version": 1,
            "strategy": "heuristic-structured-plan",
            "steps": [
                {
                    "step_id": "step_1",
                    "title": "Search literature",
                    "prompt": "Find papers on BRCA1",
                    "tools": ["bio_ncbi_search"],
                    "depends_on": [],
                    "agent_role": "research_worker",
                }
            ],
        },
        {
            "kind": "plan_recomputed",
            "plan_version": 2,
            "strategy": "heuristic-replan",
            "steps": [
                {
                    "step_id": "step_2",
                    "title": "Summarize findings",
                    "prompt": "Summarize the top hits",
                    "tools": ["summarize_chunk"],
                    "depends_on": ["step_1"],
                    "agent_role": "report_worker",
                }
            ],
        },
    ]

    plan = load_plan_from_checkpoints(checkpoints)

    assert plan is not None
    assert plan.plan_version == 2
    assert plan.strategy == "heuristic-replan"
    assert len(plan.steps) == 1
    assert plan.steps[0] == PlanStep(
        step_id="step_2",
        title="Summarize findings",
        prompt="Summarize the top hits",
        tools=["summarize_chunk"],
        depends_on=["step_1"],
        agent_role="report_worker",
        success_criteria="",
    )


def test_load_plan_from_checkpoints_returns_none_when_missing():
    assert load_plan_from_checkpoints([]) is None
    assert load_plan_from_checkpoints([{"kind": "step_done", "step_id": "step_1"}]) is None

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlanStep:
    step_id: str
    title: str
    prompt: str
    tools: list[str]
    depends_on: list[str]
    agent_role: str = "research_worker"
    success_criteria: str = ""


@dataclass
class StructuredPlan:
    plan_version: int
    steps: list[PlanStep]
    strategy: str


def build_structured_plan(prompt: str, available_tools: list[str], max_steps: int = 5) -> StructuredPlan:
    lines = [line.strip(" -\t") for line in prompt.splitlines() if line.strip()]
    if len(lines) < 2:
        segments = [part.strip() for part in prompt.split("。") if part.strip()]
        lines = segments if len(segments) >= 2 else [prompt.strip()]
    lines = lines[: max(1, max_steps)]
    steps: list[PlanStep] = []
    for idx, line in enumerate(lines, start=1):
        selected_tools = _select_tools_for_step(line, available_tools)
        agent_role = _select_agent_role(line, idx)
        steps.append(
            PlanStep(
                step_id=f"step_{idx}",
                title=line[:80] or f"Step {idx}",
                prompt=line[:1200],
                tools=selected_tools,
                depends_on=[f"step_{idx - 1}"] if idx > 1 else [],
                agent_role=agent_role,
                success_criteria=_default_success_criteria(agent_role),
            )
        )
    return StructuredPlan(plan_version=1, steps=steps, strategy="heuristic-structured-plan")


def replan_remaining_steps(
    plan: StructuredPlan,
    completed_step_ids: set[str],
    failure_step_id: str | None,
    failure_reason: str | None = None,
) -> StructuredPlan:
    remaining = [step for step in plan.steps if step.step_id not in completed_step_ids]
    if not remaining:
        return StructuredPlan(plan_version=plan.plan_version + 1, steps=[], strategy="replan-empty")
    if failure_step_id and remaining and remaining[0].step_id == failure_step_id and failure_reason:
        remaining[0].prompt = f"{remaining[0].prompt}\n\n[RecoveryHint]\nPrevious attempt failed: {failure_reason[:500]}"
    return StructuredPlan(plan_version=plan.plan_version + 1, steps=remaining, strategy="heuristic-replan")


def load_plan_from_checkpoints(checkpoints: list[dict]) -> StructuredPlan | None:
    """Restore the latest supervisor plan saved in checkpoints."""
    plan_events = [item for item in checkpoints if item.get("kind") in {"plan_created", "plan_recomputed"}]
    if not plan_events:
        return None
    latest = plan_events[-1]
    raw_steps = latest.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    steps: list[PlanStep] = []
    for idx, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            continue
        steps.append(
            PlanStep(
                step_id=str(item.get("step_id") or f"step_{idx}"),
                title=str(item.get("title") or item.get("step_id") or f"Step {idx}")[:120],
                prompt=str(item.get("prompt") or "")[:1200],
                tools=[tool for tool in item.get("tools", []) if isinstance(tool, str)],
                depends_on=[dep for dep in item.get("depends_on", []) if isinstance(dep, str)],
                agent_role=str(item.get("agent_role") or "research_worker"),
                success_criteria=str(item.get("success_criteria") or "")[:500],
            )
        )
    if not steps:
        return None
    return StructuredPlan(
        plan_version=int(latest.get("plan_version") or 1),
        steps=steps,
        strategy=str(latest.get("strategy") or "checkpoint-restore"),
    )


def _select_tools_for_step(line: str, available_tools: list[str]) -> list[str]:
    lowered = line.lower()
    selected: list[str] = []
    mapping = {
        "session_lookup": ("session", "history", "conversation", "context"),
        "summarize_chunk": ("summary", "summarize", "整理"),
        "http_search_wrapper": ("search", "web", "fetch", "http", "api"),
        "bio_ncbi_search": ("ncbi", "pubmed", "gene", "variant", "literature", "文献"),
        "bio_uniprot_lookup": ("uniprot", "protein", "accession", "蛋白"),
        "bio_spliceai_submit": ("splice", "spliceai", "剪接"),
        "bio_spliceai_get_result": ("spliceai", "score", "result"),
    }
    for tool_name in available_tools:
        keywords = mapping.get(tool_name)
        if keywords and any(keyword in lowered for keyword in keywords):
            selected.append(tool_name)
    if not selected:
        for fallback in ("session_lookup", "summarize_chunk"):
            if fallback in available_tools and fallback not in selected:
                selected.append(fallback)
    return selected[:4]


def _select_agent_role(line: str, idx: int) -> str:
    lowered = line.lower()
    if any(token in lowered for token in ("splice", "spliceai", "score", "analyze", "分析", "剪接")):
        return "analysis_worker"
    if any(token in lowered for token in ("ncbi", "pubmed", "uniprot", "lookup", "search", "gather", "检索")):
        return "research_worker"
    return "research_worker" if idx % 2 else "report_worker"


def _default_success_criteria(agent_role: str) -> str:
    if agent_role == "research_worker":
        return "Collected live database evidence with source identifiers."
    if agent_role == "analysis_worker":
        return "Produced computed analysis or SpliceAI scores."
    return "Step output summarized for downstream synthesis."

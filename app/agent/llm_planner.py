from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.agent.planner import PlanStep, StructuredPlan, build_structured_plan, replan_remaining_steps
from app.core.config import get_settings
from app.services.model_router import route_model

logger = logging.getLogger(__name__)

@dataclass
class PlanBuildInput:
    prompt: str
    available_tools: list[str]
    agent_type: str
    tenant_id: str
    model: str
    max_steps: int = 5


async def build_plan(payload: PlanBuildInput) -> StructuredPlan:
    route = route_model(
        requested_model=payload.model,
        prompt=payload.prompt,
        agent_type=payload.agent_type,
        tenant_id=payload.tenant_id,
        tools_count=len(payload.available_tools),
    )
    if route.complexity_score >= get_settings().planner_llm_complexity_threshold and payload.model != "builtin":
        llm_plan = await _build_llm_plan(payload)
        if llm_plan is not None:
            return llm_plan
    return build_structured_plan(
        prompt=payload.prompt,
        available_tools=payload.available_tools,
        max_steps=payload.max_steps,
    )


async def _build_llm_plan(payload: PlanBuildInput) -> StructuredPlan | None:
    try:
        from app.agent.openai_adapter import run_openai_agents
    except Exception as exc:
        logger.warning("llm_planner.import_failed", exc_info=exc)
        return None

    settings = get_settings()
    planner_model = settings.planner_llm_model or settings.model_router_simple_model
    instructions = (
        "You are a workflow planner for bioinformatics tasks. "
        "Return ONLY valid JSON with keys: steps (array). "
        "Each step must include: step_id, title, prompt, tools (array), depends_on (array), "
        "agent_role (research_worker|analysis_worker|report_worker), success_criteria (string)."
    )
    planner_prompt = (
        f"Agent type: {payload.agent_type}\n"
        f"Available tools: {', '.join(payload.available_tools)}\n"
        f"User task:\n{payload.prompt[:3000]}\n"
        f"Create up to {payload.max_steps} steps."
    )
    try:
        raw = await run_openai_agents(prompt=planner_prompt, model=planner_model, instructions=instructions)
    except Exception as exc:
        logger.warning(
            "llm_planner.request_failed tenant=%s agent_type=%s model=%s",
            payload.tenant_id,
            payload.agent_type,
            planner_model,
            exc_info=exc,
        )
        return None

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        logger.info("llm_planner.invalid_json tenant=%s agent_type=%s", payload.tenant_id, payload.agent_type)
        return None
    steps_raw = parsed.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        return None

    steps: list[PlanStep] = []
    for idx, item in enumerate(steps_raw[: payload.max_steps], start=1):
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("step_id") or f"step_{idx}")
        tools = [tool for tool in item.get("tools", []) if isinstance(tool, str) and tool in payload.available_tools]
        depends_on = [dep for dep in item.get("depends_on", []) if isinstance(dep, str)]
        agent_role = str(item.get("agent_role") or ("research_worker" if idx % 2 else "analysis_worker"))
        steps.append(
            PlanStep(
                step_id=step_id,
                title=str(item.get("title") or step_id)[:120],
                prompt=str(item.get("prompt") or payload.prompt)[:1200],
                tools=tools,
                depends_on=depends_on,
                agent_role=agent_role,
                success_criteria=str(item.get("success_criteria") or "")[:500],
            )
        )
    if not steps:
        return None
    return StructuredPlan(plan_version=1, steps=steps, strategy="llm-structured-plan")


def _extract_json(raw: str) -> dict | list | None:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


__all__ = ["PlanBuildInput", "build_plan", "replan_remaining_steps"]

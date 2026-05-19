import asyncio
from dataclasses import dataclass
from uuid import uuid4

from app.agent.events import build_delta_event, build_part_event, build_status_event, build_usage_event
from app.agent.skills import SKILL_REGISTRY, SkillFn
from app.core.exceptions import ApiError
from app.schemas.realtime import AgentEvent


@dataclass
class AgentRunResult:
    events: list[AgentEvent]


class SimpleAgent:
    def __init__(self, task_id: str, skill: SkillFn, model: str | None = None) -> None:
        self.task_id = task_id
        self.skill = skill
        self.model = model

    async def run(self, prompt: str) -> AgentRunResult:
        events: list[AgentEvent] = []
        events.append(
            build_status_event(
                str(uuid4()),
                self.task_id,
                "running",
                f"Agent started with model={self.model or 'builtin'}",
            )
        )

        transformed = self.skill(prompt)

        for idx, chunk in enumerate(transformed.split(" "), start=2):
            events.append(build_delta_event(str(idx), self.task_id, chunk + " "))
            await asyncio.sleep(0.02)
        events.append(build_part_event(str(uuid4()), self.task_id, "final_text", transformed))
        events.append(build_usage_event(str(uuid4()), self.task_id, len(prompt), len(transformed)))
        events.append(build_status_event(str(uuid4()), self.task_id, "success", "Agent completed"))
        return AgentRunResult(events=events)


def create_agent(task_id: str, agent_type: str, model: str | None = None) -> SimpleAgent:
    skill = SKILL_REGISTRY.get(agent_type)
    if skill is None:
        raise ApiError(status_code=400, code="UNKNOWN_AGENT_TYPE", detail=f"Unknown agent_type '{agent_type}'.")
    return SimpleAgent(task_id=task_id, skill=skill, model=model)

from collections.abc import Callable

from app.agent.skill_specs import DEFAULT_SKILL_SPECS, SkillSpec


SkillFn = Callable[[str], str]


def echo_skill(prompt: str) -> str:
    return prompt


def uppercase_skill(prompt: str) -> str:
    return prompt.upper()


SKILL_REGISTRY: dict[str, SkillFn] = {
    "echo": echo_skill,
    "uppercase": uppercase_skill,
}


def register_skill(name: str, fn: SkillFn) -> None:
    SKILL_REGISTRY[name] = fn


def list_skills() -> list[str]:
    names = {spec.name for spec in DEFAULT_SKILL_SPECS}
    names.update(SKILL_REGISTRY.keys())
    return sorted(names)


def list_skill_specs() -> list[SkillSpec]:
    return list(DEFAULT_SKILL_SPECS)

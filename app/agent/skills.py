from collections.abc import Callable


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
    return sorted(SKILL_REGISTRY.keys())

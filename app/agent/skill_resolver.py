from __future__ import annotations

from app.agent.skill_specs import DEFAULT_SKILL_SPECS, SkillSpec, get_skill_spec
from app.agent.tool_bindings import resolve_agent_tool_binding
from app.services.skill_environment import load_skill_instructions_from_disk, resolve_specs_by_names

# Always available for these agent types (merged before trigger-matched skills).
DEFAULT_AGENT_SKILLS: dict[str, tuple[str, ...]] = {
    "research": ("web-search", "session-recall"),
    "supervisor": ("web-search", "session-recall"),
    "orchestrator": ("web-search", "session-recall", "mcp-bridge"),
    "report": ("session-recall", "general-assistant"),
}


def resolve_skills(*, prompt: str, agent_type: str, max_skills: int = 2) -> list[SkillSpec]:
    lowered = prompt.lower()
    scored: list[tuple[int, SkillSpec]] = []
    for spec in DEFAULT_SKILL_SPECS:
        score = 0
        for trigger in spec.triggers:
            if trigger.lower() in lowered:
                score += 2
        if agent_type in {"research", "supervisor", "orchestrator"}:
            score += 1
        if score > 0:
            scored.append((score, spec))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    triggered = [spec for _, spec in scored[: max(0, max_skills)]]

    merged: list[SkillSpec] = []
    seen: set[str] = set()
    for name in DEFAULT_AGENT_SKILLS.get(agent_type, ()):
        spec = get_skill_spec(name)
        if spec is not None and spec.name not in seen:
            merged.append(spec)
            seen.add(spec.name)
    for spec in triggered:
        if spec.name not in seen:
            merged.append(spec)
            seen.add(spec.name)
    return merged


def build_skill_instructions(skills: list[SkillSpec]) -> str:
    if not skills:
        return ""
    blocks: list[str] = []
    for skill in skills:
        disk_body = load_skill_instructions_from_disk(skill.name)
        body = disk_body or skill.instructions
        default_hint = ""
        if skill.default_script:
            default_hint = f"\nDefault script: {skill.default_script}"
        blocks.append(f"[Skill:{skill.name}]\n{body}{default_hint}")
    return "\n\n".join(blocks)


def merged_skill_tools(skills: list[SkillSpec]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for skill in skills:
        for tool in skill.tools:
            if tool not in seen:
                seen.add(tool)
                merged.append(tool)
    return merged


def resolve_runtime_tool_names(*, agent_type: str, skill_names: list[str]) -> list[str]:
    """Runtime allowlist = agent binding ∩ tools declared by resolved skills."""
    binding = resolve_agent_tool_binding(agent_type)
    skill_tools = set(merged_skill_tools(resolve_specs_by_names(skill_names)))
    if skill_tools:
        return [name for name in binding.tools if name in skill_tools]
    return list(binding.tools)


def merged_skill_permissions(skills: list[SkillSpec]) -> set[str]:
    permissions: set[str] = set()
    for skill in skills:
        permissions.update(skill.permissions)
    return permissions


def resolve_skill_subagent_role(skill_name: str) -> str | None:
    for spec in DEFAULT_SKILL_SPECS:
        if spec.name == skill_name:
            return spec.subagent_role
    return None

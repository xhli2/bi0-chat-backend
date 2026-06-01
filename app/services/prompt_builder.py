from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.models.chat_memory_kv import ChatMemoryKV
from app.models.chat_message import ChatMessage
from app.models.chat_summary import ChatSummary
from app.models.chat_tool_call import ChatToolCall
from app.models.session_entity import SessionEntity
from app.schemas.session import AgentContextSnapshot, ContextPolicy
from app.services.context_pack_loader import load_context_pack_text
from app.agent.skill_resolver import build_skill_instructions, resolve_skills
from app.services.session_history import estimate_tokens


@dataclass
class PromptBuildInput:
    session_id: str
    tenant_id: str
    user_prompt: str
    context_policy: ContextPolicy
    summary: ChatSummary | None
    memory_items: list[ChatMemoryKV]
    recent_messages: list[ChatMessage]
    active_entities: list[SessionEntity] | None = None
    recent_tool_calls: list[ChatToolCall] | None = None
    agent_type: str = "echo"


class PromptBuilder:
    def __init__(self) -> None:
        self.settings = get_settings()

    def build(self, payload: PromptBuildInput) -> AgentContextSnapshot:
        total = self.settings.context_budget_tokens
        bucket_system = int(total * self.settings.context_ratio_system_tenant)
        bucket_memory = int(total * self.settings.context_ratio_memory_kv)
        bucket_summary = int(total * self.settings.context_ratio_summary)
        bucket_recent = int(total * self.settings.context_ratio_recent_messages)

        system_base = "You are a bioinformatics analysis assistant. Be concise, cite evidence, and state uncertainty."
        tenant_policy = f"Tenant policy id: {payload.tenant_id}. Respect tenant-level model and output policies."
        resolved_skills = resolve_skills(prompt=payload.user_prompt, agent_type=payload.agent_type)
        context_pack_text, context_pack_ids = load_context_pack_text(
            prompt=payload.user_prompt,
            agent_type=payload.agent_type,
            tenant_id=payload.tenant_id,
            skill_names=[skill.name for skill in resolved_skills],
        )
        skill_text = build_skill_instructions(resolved_skills)
        instructions_parts = [system_base, tenant_policy]
        if context_pack_text:
            instructions_parts.append(context_pack_text)
        if skill_text:
            instructions_parts.append(skill_text)
        instructions = "\n\n".join(instructions_parts)
        system_tokens = estimate_tokens(instructions)
        if system_tokens > bucket_system:
            instructions = instructions[: bucket_system * 4]
            system_tokens = estimate_tokens(instructions)

        summary_text = ""
        summary_hit = payload.summary is not None
        if payload.summary:
            summary_text = payload.summary.summary_text
            if payload.context_policy == "recent_first":
                summary_text = payload.summary.summary_short or payload.summary.summary_text
        summary_tokens = estimate_tokens(summary_text)
        if summary_tokens > bucket_summary:
            summary_text = summary_text[: bucket_summary * 4]
            summary_tokens = estimate_tokens(summary_text)

        kv_blocks: list[str] = []
        kv_tokens = 0
        trimmed_kv = 0
        for item in payload.memory_items:
            block = f"{item.key}: {item.value}"
            block_tokens = estimate_tokens(block)
            if kv_tokens + block_tokens > bucket_memory:
                trimmed_kv += 1
                continue
            kv_blocks.append(block)
            kv_tokens += block_tokens

        kv_hit = len(kv_blocks) > 0

        message_lines: list[str] = []
        recent_tokens = 0
        trimmed_recent = 0
        for msg in payload.recent_messages:
            line = f"{msg.role.upper()}: {msg.content}"
            line_tokens = estimate_tokens(line)
            if recent_tokens + line_tokens > bucket_recent:
                trimmed_recent += 1
                continue
            message_lines.append(line)
            recent_tokens += line_tokens

        if payload.context_policy == "summary_heavy" and summary_text:
            # reduce recent window bias when policy prefers summary.
            cut = max(0, len(message_lines) // 3)
            trimmed_recent += cut
            message_lines = message_lines[cut:]
            recent_tokens = estimate_tokens("\n".join(message_lines))

        context_blocks = []
        if payload.active_entities:
            entity_lines = []
            for item in payload.active_entities[:8]:
                label = item.display_name or item.canonical_id
                ref = f" ref={item.raw_ref}" if item.raw_ref else ""
                summary = f" {item.summary[:120]}" if item.summary else ""
                entity_lines.append(f"- {item.entity_type}: {label}{ref}{summary}")
            if entity_lines:
                context_blocks.append("[ActiveAnalysis]\n" + "\n".join(entity_lines))
        if payload.recent_tool_calls:
            tool_lines = []
            for item in payload.recent_tool_calls[-10:]:
                ref = f" ref={item.output_ref}" if item.output_ref else ""
                tool_lines.append(f"- turn={item.turn_index} {item.tool_name} [{item.status}]{ref}")
            if tool_lines:
                context_blocks.append("[RecentToolCalls]\n" + "\n".join(tool_lines))
        if summary_text:
            context_blocks.append(f"[SessionSummary]\n{summary_text}")
        if kv_blocks:
            context_blocks.append("[SessionMemory]\n" + "\n".join(kv_blocks))
        if message_lines:
            context_blocks.append("[RecentMessages]\n" + "\n".join(message_lines))

        used = system_tokens + summary_tokens + kv_tokens + recent_tokens
        return AgentContextSnapshot(
            session_id=payload.session_id,
            context_policy=payload.context_policy,
            summary_version=payload.summary.version if payload.summary else None,
            context_budget_tokens=total,
            context_budget_used=used,
            input_tokens_by_layer={
                "system_tenant": system_tokens,
                "summary": summary_tokens,
                "memory_kv": kv_tokens,
                "recent_messages": recent_tokens,
            },
            trimmed_items_count={
                "memory_kv": trimmed_kv,
                "recent_messages": trimmed_recent,
            },
            summary_hit=summary_hit,
            kv_hit=kv_hit,
            instructions=instructions,
            context_blocks=context_blocks,
            input_message=payload.user_prompt,
            context_pack_ids=context_pack_ids,
            skill_names=[skill.name for skill in resolved_skills],
        )

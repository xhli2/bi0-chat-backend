from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.models.chat_memory_kv import ChatMemoryKV
from app.models.chat_message import ChatMessage
from app.models.chat_summary import ChatSummary
from app.schemas.session import AgentContextSnapshot, ContextPolicy
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


class PromptBuilder:
    def __init__(self) -> None:
        self.settings = get_settings()

    def build(self, payload: PromptBuildInput) -> AgentContextSnapshot:
        total = self.settings.context_budget_tokens
        bucket_system = int(total * self.settings.context_ratio_system_tenant)
        bucket_memory = int(total * self.settings.context_ratio_memory_kv)
        bucket_summary = int(total * self.settings.context_ratio_summary)
        bucket_recent = int(total * self.settings.context_ratio_recent_messages)

        system_base = "You are a backend report generation assistant. Be concise and factual."
        tenant_policy = f"Tenant policy id: {payload.tenant_id}. Respect tenant-level model and output policies."
        instructions = f"{system_base}\n{tenant_policy}"
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
        )

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.services.token_counter import estimate_tokens_for_model


@dataclass
class ModelRouteDecision:
    selected_model: str
    requested_model: str
    complexity_score: int
    estimated_tokens: int
    token_limit: int | None
    fallback_chain: list[str]
    reason: str


def route_model(
    *,
    requested_model: str,
    prompt: str,
    agent_type: str,
    tenant_id: str,
    tools_count: int = 0,
) -> ModelRouteDecision:
    settings = get_settings()
    allowlist = settings.parsed_model_allowlist
    limits = settings.parsed_model_token_limits
    fallback_map = settings.parsed_model_fallback_chains
    auto_alias = settings.model_router_auto_alias

    score = _compute_complexity_score(prompt=prompt, agent_type=agent_type, tools_count=tools_count)
    initial_model = requested_model
    if requested_model == auto_alias:
        initial_model = (
            settings.model_router_complex_model
            if score >= settings.model_router_complexity_threshold
            else settings.model_router_simple_model
        )

    selected_model = initial_model if initial_model in allowlist else settings.model_router_simple_model
    if selected_model not in allowlist and allowlist:
        selected_model = sorted(allowlist)[0]

    estimated_tokens = estimate_tokens_for_model(prompt, model=selected_model)
    token_limit = limits.get(selected_model)
    reason = f"tenant={tenant_id}; complexity={score}; requested={requested_model}"

    if token_limit is not None and estimated_tokens > token_limit:
        for candidate in [settings.model_router_complex_model, *fallback_map.get(selected_model, [])]:
            if candidate in allowlist and limits.get(candidate, token_limit) >= estimated_tokens:
                selected_model = candidate
                token_limit = limits.get(candidate)
                reason += f"; token_escalation={candidate}"
                break

    fallback_chain = [name for name in fallback_map.get(selected_model, []) if name in allowlist and name != selected_model]
    return ModelRouteDecision(
        selected_model=selected_model,
        requested_model=requested_model,
        complexity_score=score,
        estimated_tokens=estimated_tokens,
        token_limit=token_limit,
        fallback_chain=fallback_chain,
        reason=reason,
    )


def _compute_complexity_score(*, prompt: str, agent_type: str, tools_count: int) -> int:
    lowered = prompt.lower()
    tokens_rough = max(1, len(prompt) // 4)
    score = 0
    if tokens_rough > 700:
        score += 3
    elif tokens_rough > 250:
        score += 2
    elif tokens_rough > 120:
        score += 1
    if "\n" in prompt or any(marker in lowered for marker in ["step", "步骤", "plan", "workflow", "multi"]):
        score += 2
    if agent_type in {"supervisor", "orchestrator"}:
        score += 2
    if tools_count >= 4:
        score += 2
    elif tools_count >= 2:
        score += 1
    if any(keyword in lowered for keyword in ["spliceai", "variant", "genome", "ncbi", "uniprot"]):
        score += 1
    return score

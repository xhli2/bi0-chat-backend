from app.core.config import get_settings
from app.services.model_router import route_model
from app.services.model_policy import validate_model_for_tenant


def test_model_policy_accepts_auto_alias():
    settings = get_settings()
    validate_model_for_tenant(settings.model_router_auto_alias, "public")


def test_model_router_auto_for_complex_prompt():
    decision = route_model(
        requested_model="auto",
        prompt="Step 1: collect evidence\nStep 2: compare results\nStep 3: write report with citations and error handling",
        agent_type="supervisor",
        tenant_id="public",
        tools_count=5,
    )
    assert decision.selected_model in {"gpt-4.1", "gpt-4.1-mini", "builtin"}
    assert decision.complexity_score >= 4
    assert decision.estimated_tokens > 0


def test_model_router_respects_explicit_request():
    decision = route_model(
        requested_model="builtin",
        prompt="hello",
        agent_type="echo",
        tenant_id="public",
        tools_count=1,
    )
    assert decision.selected_model == "builtin"

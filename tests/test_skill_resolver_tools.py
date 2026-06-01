from app.agent.skill_resolver import resolve_runtime_tool_names


def test_resolve_runtime_tool_names_intersects_binding_and_skills():
    tools = resolve_runtime_tool_names(
        agent_type="research",
        skill_names=["web-search", "session-recall"],
    )
    assert "http_search_wrapper" in tools
    assert "session_lookup" in tools
    assert "bio_ncbi_search" not in tools


def test_resolve_runtime_tool_names_falls_back_to_binding_when_no_skill_tools():
    tools = resolve_runtime_tool_names(agent_type="research", skill_names=[])
    assert "bio_ncbi_search" in tools
    assert "http_search_wrapper" in tools

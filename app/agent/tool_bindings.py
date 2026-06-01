from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class AgentToolBinding:
    skills: list[str]
    tools: list[str]


DEFAULT_AGENT_TOOL_BINDINGS: dict[str, AgentToolBinding] = {
    "report": AgentToolBinding(
        skills=["session-recall", "general-assistant"],
        tools=["session_lookup", "summarize_chunk", "time_now"],
    ),
    "research": AgentToolBinding(
        skills=["web-search", "session-recall", "mcp-bridge"],
        tools=[
            "session_lookup",
            "summarize_chunk",
            "time_now",
            "bio_ncbi_search",
            "bio_uniprot_lookup",
            "bio_mygene_query",
            "bio_ensembl_gene_lookup",
            "bio_ensembl_vep",
            "bio_pdb_search",
            "bio_alphafold_lookup",
            "bio_spliceai_submit",
            "bio_spliceai_get_result",
            "bio_script_runner",
            "http_search_wrapper",
            "mcp_proxy_call",
        ],
    ),
    "supervisor": AgentToolBinding(
        skills=["web-search", "session-recall", "mcp-bridge"],
        tools=[
            "session_lookup",
            "summarize_chunk",
            "time_now",
            "bio_ncbi_search",
            "bio_uniprot_lookup",
            "bio_mygene_query",
            "bio_ensembl_gene_lookup",
            "bio_ensembl_vep",
            "bio_pdb_search",
            "bio_alphafold_lookup",
            "bio_spliceai_submit",
            "bio_spliceai_get_result",
            "bio_script_runner",
            "http_search_wrapper",
            "mcp_proxy_call",
        ],
    ),
    "orchestrator": AgentToolBinding(
        skills=["web-search", "session-recall", "mcp-bridge"],
        tools=[
            "session_lookup",
            "summarize_chunk",
            "time_now",
            "bio_ncbi_search",
            "bio_uniprot_lookup",
            "bio_mygene_query",
            "bio_ensembl_gene_lookup",
            "bio_ensembl_vep",
            "bio_pdb_search",
            "bio_alphafold_lookup",
            "bio_spliceai_submit",
            "bio_spliceai_get_result",
            "bio_script_runner",
            "http_search_wrapper",
            "mcp_proxy_call",
        ],
    ),
    "echo": AgentToolBinding(skills=["general-assistant"], tools=["time_now"]),
}


def resolve_agent_tool_binding(agent_type: str) -> AgentToolBinding:
    settings = get_settings()
    configured = settings.parsed_agent_tool_bindings
    if agent_type in configured:
        item = configured[agent_type]
        return AgentToolBinding(skills=item.get("skills", []), tools=item.get("tools", []))
    if agent_type in DEFAULT_AGENT_TOOL_BINDINGS:
        return DEFAULT_AGENT_TOOL_BINDINGS[agent_type]
    return AgentToolBinding(skills=["echo"], tools=["time_now"])

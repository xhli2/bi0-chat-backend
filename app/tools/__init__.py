from app.core.config import get_settings
from app.tools.adapters.http_wrapper import (
    HttpSearchWrapperInput,
    HttpSearchWrapperOutput,
    tool_http_search_wrapper,
)
from app.tools.adapters.mcp_proxy import MCPProxyCallInput, MCPProxyCallOutput, tool_mcp_proxy_call
from app.tools.adapters.bio_script_runner import BioScriptRunInput, BioScriptRunOutput, tool_bio_script_runner
from app.tools.adapters.spliceai_wrapper import (
    SpliceAIGetResultInput,
    SpliceAIGetResultOutput,
    SpliceAISubmitInput,
    SpliceAISubmitOutput,
    tool_spliceai_get_result,
    tool_spliceai_submit,
)
from app.tools.adapters.bio_public import (
    NCBISearchInput,
    NCBISearchOutput,
    UniProtLookupInput,
    UniProtLookupOutput,
    tool_bio_ncbi_search,
    tool_bio_uniprot_lookup,
)
from app.tools.adapters.bio_extended import (
    AlphaFoldLookupInput,
    AlphaFoldLookupOutput,
    EnsemblGeneLookupInput,
    EnsemblGeneLookupOutput,
    EnsemblVepInput,
    EnsemblVepOutput,
    MyGeneQueryInput,
    MyGeneQueryOutput,
    PdbSearchInput,
    PdbSearchOutput,
    tool_bio_alphafold_lookup,
    tool_bio_ensembl_gene_lookup,
    tool_bio_ensembl_vep,
    tool_bio_mygene_query,
    tool_bio_pdb_search,
)
from app.tools.builtin.core_tools import (
    SessionLookupInput,
    SessionLookupOutput,
    SummarizeChunkInput,
    SummarizeChunkOutput,
    TimeNowInput,
    TimeNowOutput,
    tool_session_lookup,
    tool_summarize_chunk,
    tool_time_now,
)
from app.tools.registry import ToolSpec, tool_registry

settings = get_settings()


def register_default_tools() -> None:
    if tool_registry.get("time_now") is not None:
        return

    tool_registry.register(
        ToolSpec(
            name="time_now",
            description="Get current UTC time in ISO format.",
            input_schema=TimeNowInput,
            output_schema=TimeNowOutput,
            required_permissions=set(),
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_time_now,
            safe_for_public_tenant=True,
            provider="function",
            risk_level="low",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="session_lookup",
            description="Read recent session context and latest summary.",
            input_schema=SessionLookupInput,
            output_schema=SessionLookupOutput,
            required_permissions={"session:read"},
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_session_lookup,
            safe_for_public_tenant=True,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="summarize_chunk",
            description="Summarize a text chunk with character budget.",
            input_schema=SummarizeChunkInput,
            output_schema=SummarizeChunkOutput,
            required_permissions=set(),
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_summarize_chunk,
            safe_for_public_tenant=True,
            provider="function",
            risk_level="low",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_ncbi_search",
            description="Search NCBI Entrez database (default PubMed) and return accession IDs.",
            input_schema=NCBISearchInput,
            output_schema=NCBISearchOutput,
            required_permissions={"bio:ncbi:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 15),
            executor=tool_bio_ncbi_search,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_uniprot_lookup",
            description="Query UniProt proteins by accession/gene and return normalized records.",
            input_schema=UniProtLookupInput,
            output_schema=UniProtLookupOutput,
            required_permissions={"bio:uniprot:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 15),
            executor=tool_bio_uniprot_lookup,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_spliceai_submit",
            description="Submit an async SpliceAI scoring job for variant effect prediction.",
            input_schema=SpliceAISubmitInput,
            output_schema=SpliceAISubmitOutput,
            required_permissions={"bio:spliceai:submit"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 15),
            executor=tool_spliceai_submit,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="high",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_script_runner",
            description="Run an approved tenant script inside an isolated workspace with resource limits.",
            input_schema=BioScriptRunInput,
            output_schema=BioScriptRunOutput,
            required_permissions={"bio:script:run"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_max, settings.script_runner_timeout_seconds + 5),
            executor=tool_bio_script_runner,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="high",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_mygene_query",
            description="Query MyGene.info for gene symbols, Entrez IDs, Ensembl IDs, and summaries.",
            input_schema=MyGeneQueryInput,
            output_schema=MyGeneQueryOutput,
            required_permissions={"bio:annotation:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 15),
            executor=tool_bio_mygene_query,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_ensembl_gene_lookup",
            description="Resolve a gene symbol to Ensembl gene metadata via Ensembl REST.",
            input_schema=EnsemblGeneLookupInput,
            output_schema=EnsemblGeneLookupOutput,
            required_permissions={"bio:annotation:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 15),
            executor=tool_bio_ensembl_gene_lookup,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_ensembl_vep",
            description="Annotate a variant with Ensembl VEP using HGVS notation.",
            input_schema=EnsemblVepInput,
            output_schema=EnsemblVepOutput,
            required_permissions={"bio:annotation:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 20),
            executor=tool_bio_ensembl_vep,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_pdb_search",
            description="Search RCSB PDB for experimental protein structures by text query.",
            input_schema=PdbSearchInput,
            output_schema=PdbSearchOutput,
            required_permissions={"bio:structure:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 15),
            executor=tool_bio_pdb_search,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_alphafold_lookup",
            description="Fetch AlphaFold DB predicted structure metadata for a UniProt accession.",
            input_schema=AlphaFoldLookupInput,
            output_schema=AlphaFoldLookupOutput,
            required_permissions={"bio:structure:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 15),
            executor=tool_bio_alphafold_lookup,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="bio_spliceai_get_result",
            description="Fetch archived result for a submitted SpliceAI job.",
            input_schema=SpliceAIGetResultInput,
            output_schema=SpliceAIGetResultOutput,
            required_permissions={"bio:spliceai:read"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 10),
            executor=tool_spliceai_get_result,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="http_search_wrapper",
            description="Fetch HTTP content via local adapter wrapper.",
            input_schema=HttpSearchWrapperInput,
            output_schema=HttpSearchWrapperOutput,
            required_permissions={"http:external"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 10),
            executor=tool_http_search_wrapper,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="high",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="mcp_proxy_call",
            description="Call an MCP tool via configured proxy gateway.",
            input_schema=MCPProxyCallInput,
            output_schema=MCPProxyCallOutput,
            required_permissions={"mcp:invoke"},
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_mcp_proxy_call,
            safe_for_public_tenant=False,
            provider="mcp",
            risk_level="high",
        )
    )


register_default_tools()

__all__ = ["tool_registry", "register_default_tools"]

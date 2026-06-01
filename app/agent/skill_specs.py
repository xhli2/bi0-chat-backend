from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    triggers: tuple[str, ...]
    tools: tuple[str, ...]
    instructions: str
    subagent_role: str = "research_worker"
    context_pack_ids: tuple[str, ...] = ()
    bundled_scripts: tuple[str, ...] = ()
    default_script: str | None = None
    permissions: tuple[str, ...] = ()


DEFAULT_SKILL_SPECS: tuple[SkillSpec, ...] = (
    SkillSpec(
        name="variant-interpretation",
        description="Structured variant interpretation workflow using live database evidence.",
        triggers=("variant", "hgvs", "pathogenic", "致病", "变异", "brca", "c."),
        tools=("bio_ncbi_search", "bio_uniprot_lookup", "bio_spliceai_submit", "bio_spliceai_get_result"),
        instructions=(
            "Follow variant interpretation SOP: confirm identifiers, gather NCBI/UniProt evidence, "
            "assess splice impact when relevant, and summarize with source timestamps."
        ),
        subagent_role="research_worker",
        context_pack_ids=("domains/variant-analysis",),
        bundled_scripts=("summarize_evidence.py",),
        default_script="summarize_evidence.py",
    ),
    SkillSpec(
        name="protein-lookup",
        description="Protein and accession lookup via UniProt with normalized output.",
        triggers=("uniprot", "protein", "accession", "蛋白", "p0"),
        tools=("bio_uniprot_lookup",),
        instructions="Use UniProt lookup tools. Report accession, names, organism, and length clearly.",
        subagent_role="research_worker",
        context_pack_ids=("domains/protein-lookup",),
    ),
    SkillSpec(
        name="splice-analysis",
        description="Submit and retrieve SpliceAI scoring jobs for splice impact assessment.",
        triggers=("splice", "canonical", "剪接", "spliceai"),
        tools=("bio_spliceai_submit", "bio_spliceai_get_result", "bio_script_runner"),
        instructions=(
            "Validate HGVS and genome build before submitting SpliceAI jobs. "
            "Use check_hgvs.py in workspace when format is uncertain."
        ),
        subagent_role="analysis_worker",
        context_pack_ids=("domains/splice-analysis",),
        bundled_scripts=("check_hgvs.py",),
        default_script="check_hgvs.py",
        permissions=("bio:script:run",),
    ),
    SkillSpec(
        name="literature-triage",
        description="Literature search and triage via NCBI Entrez.",
        triggers=("pubmed", "literature", "文献", "ncbi"),
        tools=("bio_ncbi_search",),
        instructions="Search PubMed/NCBI first; summarize top relevant records with IDs.",
        subagent_role="research_worker",
    ),
    SkillSpec(
        name="vcf-qc",
        description="Run lightweight VCF quality checks in an isolated script workspace.",
        triggers=("vcf", "quality control", "qc", "质控", "vcf.gz"),
        tools=("bio_script_runner",),
        instructions=(
            "Place the VCF under inputs/ in the workspace, then run vcf_qc.py via bio_script_runner. "
            "Report PASS/WARN/FAIL counts and missing-field issues."
        ),
        subagent_role="analysis_worker",
        context_pack_ids=("domains/vcf-qc",),
        bundled_scripts=("vcf_qc.py",),
        default_script="vcf_qc.py",
        permissions=("bio:script:run",),
    ),
    SkillSpec(
        name="report-synthesis",
        description="Synthesize multi-source bio evidence into a structured clinical-style report.",
        triggers=("report", "synthesis", "summarize findings", "报告", "综合"),
        tools=("bio_script_runner", "summarize_chunk"),
        instructions=(
            "Collect evidence from prior tool outputs or session memory, then run build_report.py "
            "with a JSON evidence file under inputs/. Keep uncertainty explicit."
        ),
        subagent_role="report_worker",
        context_pack_ids=("domains/report-synthesis",),
        bundled_scripts=("build_report.py",),
        default_script="build_report.py",
        permissions=("bio:script:run",),
    ),
    SkillSpec(
        name="cohort-gene-search",
        description="Multi-gene panel lookup across NCBI and UniProt for cohort studies.",
        triggers=("gene panel", "cohort", "multi-gene", "基因 panel", "panel"),
        tools=("bio_ncbi_search", "bio_uniprot_lookup"),
        instructions=(
            "For each gene symbol, search NCBI literature and UniProt accessions. "
            "Return a table with gene, top PMID IDs, and UniProt accession."
        ),
        subagent_role="research_worker",
        context_pack_ids=("domains/cohort-gene-search",),
    ),
    SkillSpec(
        name="gene-annotation",
        description="Aggregate gene annotation from MyGene.info and Ensembl REST.",
        triggers=("gene annotation", "mygene", "ensembl", "entrez", "基因注释", "基因信息", "symbol lookup"),
        tools=("bio_mygene_query", "bio_ensembl_gene_lookup"),
        instructions=(
            "Start with bio_mygene_query for symbol/Entrez resolution, then bio_ensembl_gene_lookup "
            "for coordinates and biotype. Report symbol, Entrez, Ensembl ID, and locus."
        ),
        subagent_role="research_worker",
        context_pack_ids=("domains/gene-annotation",),
    ),
    SkillSpec(
        name="variant-consequence",
        description="Predict variant consequence with Ensembl VEP (HGVS input).",
        triggers=("vep", "consequence", "ensembl vep", "变异后果", "变异注释", "annotate variant", "impact"),
        tools=("bio_ensembl_vep",),
        instructions=(
            "Use bio_ensembl_vep with HGVS (e.g. 9:g.22125504G>C). "
            "Summarize most_severe_consequence, gene, transcript, and impact."
        ),
        subagent_role="analysis_worker",
        context_pack_ids=("domains/variant-consequence",),
    ),
    SkillSpec(
        name="structure-lookup",
        description="Find experimental PDB structures and AlphaFold predicted models.",
        triggers=("pdb", "alphafold", "protein structure", "3d structure", "结构", "晶体", "alphafold db"),
        tools=("bio_pdb_search", "bio_alphafold_lookup"),
        instructions=(
            "Use bio_pdb_search for experimental structures; use bio_alphafold_lookup when a UniProt "
            "accession is known. Include PDB ID or AlphaFold model URL when available."
        ),
        subagent_role="research_worker",
        context_pack_ids=("domains/structure-lookup",),
    ),
    SkillSpec(
        name="clinvar-lookup",
        description="Search ClinVar records for variant clinical significance via NCBI.",
        triggers=("clinvar", "clinical significance", "pathogenic", "benign", "临床意义", "致病性", "clinvar id"),
        tools=("bio_ncbi_search",),
        instructions=(
            "Use bio_ncbi_search with db=clinvar. Include variant/gene terms in the query. "
            "Report ClinVar IDs and note that full interpretation requires record review."
        ),
        subagent_role="research_worker",
        context_pack_ids=("domains/clinvar-lookup",),
    ),
    SkillSpec(
        name="sequence-utils",
        description="Run lightweight FASTA/sequence statistics in an isolated workspace.",
        triggers=("fasta", "sequence stats", "gc content", "序列", "fasta", "gc含量", "碱基"),
        tools=("bio_script_runner",),
        instructions=(
            "Place sequence text in inputs/seq.fasta, then run seq_stats.py via bio_script_runner. "
            "Report length, GC%, and base counts."
        ),
        subagent_role="analysis_worker",
        context_pack_ids=("domains/sequence-utils",),
        bundled_scripts=("seq_stats.py",),
        default_script="seq_stats.py",
        permissions=("bio:script:run",),
    ),
    SkillSpec(
        name="web-search",
        description="Fetch and summarize content from allowed external HTTP(S) URLs.",
        triggers=("web search", "fetch url", "http", "https", "website", "网页", "链接", "打开", "browse"),
        tools=("http_search_wrapper",),
        instructions=(
            "Use http_search_wrapper only for http/https URLs on the tenant allowlist. "
            "Quote the fetched URL, status code, and a concise summary of body_preview."
        ),
        subagent_role="research_worker",
        context_pack_ids=("domains/web-search",),
        permissions=("http:external",),
    ),
    SkillSpec(
        name="session-recall",
        description="Recall prior session messages and summaries for continuity.",
        triggers=("previous", "earlier", "history", "recall", "context", "before", "之前", "历史", "上文", "刚才"),
        tools=("session_lookup", "summarize_chunk"),
        instructions=(
            "Call session_lookup before answering questions about prior turns. "
            "Use summarize_chunk to compress long excerpts."
        ),
        subagent_role="research_worker",
        context_pack_ids=("domains/session-recall",),
        permissions=("session:read",),
    ),
    SkillSpec(
        name="mcp-bridge",
        description="Invoke external capabilities exposed via the MCP proxy gateway.",
        triggers=("mcp", "plugin", "external tool", "tool server", "插件", "外部工具", "proxy"),
        tools=("mcp_proxy_call",),
        instructions=(
            "Use mcp_proxy_call with the configured server/tool names. "
            "State which MCP server and tool were invoked and summarize the response."
        ),
        subagent_role="analysis_worker",
        context_pack_ids=("domains/mcp-bridge",),
        permissions=("mcp:invoke",),
    ),
    SkillSpec(
        name="general-assistant",
        description="General utilities: current time, quick summarization, light session lookup.",
        triggers=("time", "date", "now", "summarize", "shorten", "tl;dr", "时间", "日期", "总结", "概括"),
        tools=("time_now", "summarize_chunk", "session_lookup"),
        instructions=(
            "Use time_now for clock questions. Prefer summarize_chunk for long text compression. "
            "Use session_lookup when the user refers to this conversation implicitly."
        ),
        subagent_role="report_worker",
        permissions=("session:read",),
    ),
)


def get_skill_spec(name: str) -> SkillSpec | None:
    for spec in DEFAULT_SKILL_SPECS:
        if spec.name == name:
            return spec
    return None

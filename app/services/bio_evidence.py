from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.bio_evidence import BioEvidence, BioEvidencePack


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def attach_evidence(output: dict[str, Any], evidence: BioEvidence) -> dict[str, Any]:
    payload = dict(output)
    payload["evidence"] = evidence.model_dump(mode="python")
    pack = BioEvidencePack(items=[evidence])
    payload["evidence_pack"] = pack.model_dump(mode="python")
    return payload


def evidence_from_ncbi(*, term: str, db: str, ids: list[str], retrieved_at: str) -> BioEvidence:
    return BioEvidence(
        source="ncbi",
        entity_type="literature" if db == "pubmed" else "gene",
        identifiers={"term": term, "db": db, "count": str(len(ids)), "top_id": ids[0] if ids else ""},
        retrieved_at=retrieved_at,
        confidence=0.9 if ids else 0.3,
        summary=f"Found {len(ids)} record(s) in {db}",
        raw_ref=f"ncbi:{db}:{term}",
    )


def evidence_from_uniprot(*, query: str, records: list[dict[str, Any]], retrieved_at: str) -> BioEvidence:
    top = records[0] if records else {}
    identifiers = {"query": query}
    if top.get("accession"):
        identifiers["accession"] = str(top["accession"])
    if top.get("gene_names"):
        identifiers["gene"] = ",".join(top["gene_names"][:3])
    return BioEvidence(
        source="uniprot",
        entity_type="protein",
        identifiers=identifiers,
        retrieved_at=retrieved_at,
        confidence=0.95 if records else 0.2,
        summary=f"Matched {len(records)} UniProt record(s)",
        raw_ref=f"uniprot:{query}",
    )


def evidence_from_spliceai_job(*, job_id: str, variant_hgvs: str, genome_build: str, retrieved_at: str) -> BioEvidence:
    return BioEvidence(
        source="spliceai",
        entity_type="job",
        identifiers={"job_id": job_id, "variant_hgvs": variant_hgvs},
        genome_build=genome_build,
        retrieved_at=retrieved_at,
        confidence=1.0,
        summary="SpliceAI job queued",
        raw_ref=f"spliceai:job:{job_id}",
    )


def evidence_from_spliceai_result(*, job_id: str, variant_hgvs: str, genome_build: str, result, retrieved_at: str) -> BioEvidence:
    max_score = result.score_breakdown.max_score
    return BioEvidence(
        source="spliceai",
        entity_type="job",
        identifiers={
            "job_id": job_id,
            "variant_hgvs": variant_hgvs,
            "status": "success",
            "max_score": str(max_score),
            "predicted_impact": result.predicted_impact,
        },
        genome_build=genome_build,
        retrieved_at=retrieved_at,
        confidence=min(1.0, max(0.0, max_score)),
        summary=f"SpliceAI completed: {result.predicted_impact} impact (max_score={max_score})",
        raw_ref=f"spliceai:job:{job_id}",
    )


def evidence_from_mygene(*, query: str, hits: list[dict[str, Any]], retrieved_at: str) -> BioEvidence:
    top = hits[0] if hits else {}
    identifiers = {"query": query}
    if top.get("symbol"):
        identifiers["symbol"] = str(top["symbol"])
    if top.get("entrezgene") is not None:
        identifiers["entrez"] = str(top["entrezgene"])
    return BioEvidence(
        source="mygene",
        entity_type="gene",
        identifiers=identifiers,
        retrieved_at=retrieved_at,
        confidence=0.9 if hits else 0.2,
        summary=f"MyGene matched {len(hits)} gene record(s)",
        raw_ref=f"mygene:{query}",
    )


def evidence_from_ensembl_gene(*, symbol: str, records: list[dict[str, Any]], retrieved_at: str) -> BioEvidence:
    top = records[0] if records else {}
    identifiers = {"symbol": symbol}
    if top.get("id"):
        identifiers["ensembl_id"] = str(top["id"])
    return BioEvidence(
        source="ensembl",
        entity_type="gene",
        identifiers=identifiers,
        genome_build=top.get("assembly_name"),
        retrieved_at=retrieved_at,
        confidence=0.95 if records else 0.2,
        summary=f"Ensembl lookup returned {len(records)} gene record(s)",
        raw_ref=f"ensembl:gene:{symbol}",
    )


def evidence_from_ensembl_vep(
    *,
    variant_hgvs: str,
    consequences: list[dict[str, Any]],
    assembly_name: str | None,
    retrieved_at: str,
) -> BioEvidence:
    top = consequences[0] if consequences else {}
    identifiers = {"variant_hgvs": variant_hgvs}
    if top.get("most_severe_consequence"):
        identifiers["consequence"] = str(top["most_severe_consequence"])
    if top.get("gene_symbol"):
        identifiers["gene"] = str(top["gene_symbol"])
    return BioEvidence(
        source="ensembl-vep",
        entity_type="variant",
        identifiers=identifiers,
        genome_build=assembly_name,
        retrieved_at=retrieved_at,
        confidence=0.9 if consequences else 0.3,
        summary=f"VEP returned {len(consequences)} transcript consequence(s)",
        raw_ref=f"ensembl:vep:{variant_hgvs}",
    )


def evidence_from_pdb(*, query: str, entries: list[dict[str, Any]], retrieved_at: str) -> BioEvidence:
    top = entries[0] if entries else {}
    identifiers = {"query": query}
    if top.get("pdb_id"):
        identifiers["pdb_id"] = str(top["pdb_id"])
    return BioEvidence(
        source="rcsb-pdb",
        entity_type="protein",
        identifiers=identifiers,
        retrieved_at=retrieved_at,
        confidence=0.85 if entries else 0.2,
        summary=f"PDB search matched {len(entries)} structure(s)",
        raw_ref=f"pdb:search:{query}",
    )


def evidence_from_alphafold(*, uniprot_accession: str, models: list[dict[str, Any]], retrieved_at: str) -> BioEvidence:
    top = models[0] if models else {}
    identifiers = {"accession": uniprot_accession}
    if top.get("model_identifier"):
        identifiers["model"] = str(top["model_identifier"])
    return BioEvidence(
        source="alphafold",
        entity_type="protein",
        identifiers=identifiers,
        retrieved_at=retrieved_at,
        confidence=0.9 if models else 0.2,
        summary=f"AlphaFold returned {len(models)} predicted model(s)",
        raw_ref=f"alphafold:{uniprot_accession}",
    )


def evidence_from_variant(*, variant_hgvs: str, genome_build: str, source: str, retrieved_at: str, summary: str | None = None) -> BioEvidence:
    return BioEvidence(
        source=source,
        entity_type="variant",
        identifiers={"variant_hgvs": variant_hgvs},
        genome_build=genome_build,
        retrieved_at=retrieved_at,
        confidence=1.0,
        summary=summary or f"Active variant {variant_hgvs}",
        raw_ref=f"variant:{variant_hgvs}",
    )

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from app.services.bio_evidence import (
    attach_evidence,
    evidence_from_alphafold,
    evidence_from_ensembl_gene,
    evidence_from_ensembl_vep,
    evidence_from_mygene,
    evidence_from_pdb,
)
from app.tools.schemas import ToolExecutionError


class MyGeneQueryInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    species: str = Field(default="human", max_length=40)
    size: int = Field(default=5, ge=1, le=20)
    fields: str = Field(
        default="symbol,name,entrezgene,ensembl.gene,summary,type_of_gene",
        max_length=300,
    )


class MyGeneHit(BaseModel):
    query: str
    symbol: str | None = None
    name: str | None = None
    entrezgene: int | None = None
    ensembl_gene: str | None = None
    type_of_gene: str | None = None
    summary: str | None = None


class MyGeneQueryOutput(BaseModel):
    query: str
    species: str
    total: int
    hits: list[MyGeneHit]
    source: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


class EnsemblGeneLookupInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=80)
    species: str = Field(default="homo_sapiens", max_length=40)
    expand: bool = Field(default=False)


class EnsemblGeneRecord(BaseModel):
    id: str
    symbol: str | None = None
    biotype: str | None = None
    description: str | None = None
    assembly_name: str | None = None
    seq_region_name: str | None = None
    start: int | None = None
    end: int | None = None
    strand: int | None = None


class EnsemblGeneLookupOutput(BaseModel):
    symbol: str
    species: str
    records: list[EnsemblGeneRecord]
    source: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


class EnsemblVepInput(BaseModel):
    variant_hgvs: str = Field(min_length=5, max_length=200)
    species: str = Field(default="human", max_length=40)


class EnsemblVepConsequence(BaseModel):
    most_severe_consequence: str | None = None
    gene_symbol: str | None = None
    transcript_id: str | None = None
    impact: str | None = None
    amino_acids: str | None = None
    codons: str | None = None


class EnsemblVepOutput(BaseModel):
    variant_hgvs: str
    assembly_name: str | None = None
    consequences: list[EnsemblVepConsequence]
    source: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


class PdbSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    rows: int = Field(default=10, ge=1, le=25)


class PdbEntry(BaseModel):
    pdb_id: str
    title: str | None = None
    resolution: float | None = None
    method: str | None = None
    organism: str | None = None


class PdbSearchOutput(BaseModel):
    query: str
    total_count: int
    entries: list[PdbEntry]
    source: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


class AlphaFoldLookupInput(BaseModel):
    uniprot_accession: str = Field(min_length=4, max_length=20, pattern=r"^[A-Z0-9]+$")


class AlphaFoldModel(BaseModel):
    uniprot_accession: str
    model_identifier: str | None = None
    model_url: str | None = None
    gene: str | None = None
    organism: str | None = None
    sequence_length: int | None = None
    confidence_avg: float | None = None


class AlphaFoldLookupOutput(BaseModel):
    uniprot_accession: str
    models: list[AlphaFoldModel]
    source: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


def _retrieved_at() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_json(url: str, *, params: dict | None = None, headers: dict | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise ToolExecutionError("BIO_UPSTREAM_ERROR", f"Request failed for {url}: {exc}", retryable=True) from exc


async def _post_json(url: str, payload: dict[str, Any]) -> Any:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise ToolExecutionError("BIO_UPSTREAM_ERROR", f"Request failed for {url}: {exc}", retryable=True) from exc


async def tool_bio_mygene_query(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = _
    payload = MyGeneQueryInput.model_validate(args)
    params = {
        "q": payload.query,
        "species": payload.species,
        "size": payload.size,
        "fields": payload.fields,
    }
    data = await _get_json("https://mygene.info/v3/query", params=params)
    hits: list[MyGeneHit] = []
    for item in data.get("hits", []):
        if not isinstance(item, dict):
            continue
        ensembl = item.get("ensembl")
        ensembl_gene = None
        if isinstance(ensembl, dict) and isinstance(ensembl.get("gene"), str):
            ensembl_gene = ensembl["gene"]
        elif isinstance(ensembl, list) and ensembl and isinstance(ensembl[0], dict):
            gene_val = ensembl[0].get("gene")
            if isinstance(gene_val, str):
                ensembl_gene = gene_val
        summary = item.get("summary")
        if isinstance(summary, list):
            summary = summary[0] if summary else None
        hits.append(
            MyGeneHit(
                query=payload.query,
                symbol=item.get("symbol") if isinstance(item.get("symbol"), str) else None,
                name=item.get("name") if isinstance(item.get("name"), str) else None,
                entrezgene=item.get("entrezgene") if isinstance(item.get("entrezgene"), int) else None,
                ensembl_gene=ensembl_gene,
                type_of_gene=item.get("type_of_gene") if isinstance(item.get("type_of_gene"), str) else None,
                summary=summary if isinstance(summary, str) else None,
            )
        )
    total = data.get("total")
    if not isinstance(total, int):
        total = len(hits)
    retrieved_at = _retrieved_at()
    output = MyGeneQueryOutput(
        query=payload.query,
        species=payload.species,
        total=total,
        hits=hits,
        source="mygene.info",
        retrieved_at=retrieved_at,
    ).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_mygene(query=payload.query, hits=[hit.model_dump(mode="python") for hit in hits], retrieved_at=retrieved_at),
    )


async def tool_bio_ensembl_gene_lookup(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = _
    payload = EnsemblGeneLookupInput.model_validate(args)
    symbol = quote(payload.symbol.strip(), safe="")
    url = f"https://rest.ensembl.org/lookup/symbol/{payload.species}/{symbol}"
    params = {"expand": "1" if payload.expand else "0"}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    data = await _get_json(url, params=params, headers=headers)
    records: list[EnsemblGeneRecord] = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        gene_id = item.get("id")
        if not isinstance(gene_id, str):
            continue
        records.append(
            EnsemblGeneRecord(
                id=gene_id,
                symbol=item.get("display_name") if isinstance(item.get("display_name"), str) else payload.symbol,
                biotype=item.get("biotype") if isinstance(item.get("biotype"), str) else None,
                description=item.get("description") if isinstance(item.get("description"), str) else None,
                assembly_name=item.get("assembly_name") if isinstance(item.get("assembly_name"), str) else None,
                seq_region_name=item.get("seq_region_name") if isinstance(item.get("seq_region_name"), str) else None,
                start=item.get("start") if isinstance(item.get("start"), int) else None,
                end=item.get("end") if isinstance(item.get("end"), int) else None,
                strand=item.get("strand") if isinstance(item.get("strand"), int) else None,
            )
        )
    retrieved_at = _retrieved_at()
    output = EnsemblGeneLookupOutput(
        symbol=payload.symbol,
        species=payload.species,
        records=records,
        source="ensembl-rest",
        retrieved_at=retrieved_at,
    ).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_ensembl_gene(
            symbol=payload.symbol,
            records=[record.model_dump(mode="python") for record in records],
            retrieved_at=retrieved_at,
        ),
    )


async def tool_bio_ensembl_vep(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = _
    payload = EnsemblVepInput.model_validate(args)
    url = f"https://rest.ensembl.org/vep/{payload.species}/hgvs"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    body = {"hgvs_notations": [payload.variant_hgvs.strip()]}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.text[:300]
        except Exception:
            pass
        raise ToolExecutionError(
            "ENSEMBL_VEP_INVALID",
            f"Ensembl VEP rejected HGVS '{payload.variant_hgvs}': {detail or exc}",
            retryable=False,
        ) from exc
    except httpx.HTTPError as exc:
        raise ToolExecutionError("BIO_UPSTREAM_ERROR", f"Ensembl VEP request failed: {exc}", retryable=True) from exc
    items = data if isinstance(data, list) else [data]
    consequences: list[EnsemblVepConsequence] = []
    assembly_name = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if assembly_name is None and isinstance(item.get("assembly_name"), str):
            assembly_name = item["assembly_name"]
        for tc in item.get("transcript_consequences", [])[:8]:
            if not isinstance(tc, dict):
                continue
            consequences.append(
                EnsemblVepConsequence(
                    most_severe_consequence=tc.get("consequence_terms", [None])[0]
                    if isinstance(tc.get("consequence_terms"), list) and tc.get("consequence_terms")
                    else None,
                    gene_symbol=tc.get("gene_symbol") if isinstance(tc.get("gene_symbol"), str) else None,
                    transcript_id=tc.get("transcript_id") if isinstance(tc.get("transcript_id"), str) else None,
                    impact=tc.get("impact") if isinstance(tc.get("impact"), str) else None,
                    amino_acids=tc.get("amino_acids") if isinstance(tc.get("amino_acids"), str) else None,
                    codons=tc.get("codons") if isinstance(tc.get("codons"), str) else None,
                )
            )
    retrieved_at = _retrieved_at()
    output = EnsemblVepOutput(
        variant_hgvs=payload.variant_hgvs,
        assembly_name=assembly_name,
        consequences=consequences,
        source="ensembl-vep",
        retrieved_at=retrieved_at,
    ).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_ensembl_vep(
            variant_hgvs=payload.variant_hgvs,
            consequences=[item.model_dump(mode="python") for item in consequences],
            assembly_name=assembly_name,
            retrieved_at=retrieved_at,
        ),
    )


async def tool_bio_pdb_search(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = _
    payload = PdbSearchInput.model_validate(args)
    body = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": payload.query},
        },
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": payload.rows}, "results_content_type": ["experimental"]},
    }
    data = await _post_json("https://search.rcsb.org/rcsbsearch/v2/query", body)
    entries: list[PdbEntry] = []
    for item in data.get("result_set", []):
        if not isinstance(item, dict):
            continue
        pdb_id = item.get("identifier")
        if not isinstance(pdb_id, str):
            continue
        services = item.get("services") or []
        title = None
        resolution = None
        method = None
        organism = None
        if isinstance(services, list):
            for service in services:
                if not isinstance(service, dict):
                    continue
                nodes = service.get("nodes") or {}
                if isinstance(nodes, dict):
                    if title is None and isinstance(nodes.get("rcsb_entry_info"), dict):
                        title = nodes["rcsb_entry_info"].get("title")
                    if resolution is None and isinstance(nodes.get("rcsb_entry_info"), dict):
                        resolution = nodes["rcsb_entry_info"].get("resolution_combined")
                    if method is None and isinstance(nodes.get("exptl"), list) and nodes["exptl"]:
                        method_item = nodes["exptl"][0]
                        if isinstance(method_item, dict):
                            method = method_item.get("method")
                    if organism is None and isinstance(nodes.get("rcsb_entity_source_organism"), list) and nodes["rcsb_entity_source_organism"]:
                        org_item = nodes["rcsb_entity_source_organism"][0]
                        if isinstance(org_item, dict):
                            organism = org_item.get("ncbi_scientific_name")
        entries.append(
            PdbEntry(
                pdb_id=pdb_id.upper(),
                title=title if isinstance(title, str) else None,
                resolution=float(resolution) if isinstance(resolution, (int, float)) else None,
                method=method if isinstance(method, str) else None,
                organism=organism if isinstance(organism, str) else None,
            )
        )
    total_count = data.get("total_count")
    if not isinstance(total_count, int):
        total_count = len(entries)
    retrieved_at = _retrieved_at()
    output = PdbSearchOutput(
        query=payload.query,
        total_count=total_count,
        entries=entries,
        source="rcsb-search",
        retrieved_at=retrieved_at,
    ).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_pdb(query=payload.query, entries=[entry.model_dump(mode="python") for entry in entries], retrieved_at=retrieved_at),
    )


async def tool_bio_alphafold_lookup(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = _
    payload = AlphaFoldLookupInput.model_validate(args)
    accession = payload.uniprot_accession.upper()
    url = f"https://alphafold.ebi.ac.uk/api/prediction/{accession}"
    data = await _get_json(url)
    items = data if isinstance(data, list) else [data]
    models: list[AlphaFoldModel] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uniprot_id = item.get("uniprotAccession") or item.get("uniprotId")
        if not isinstance(uniprot_id, str):
            uniprot_id = accession
        models.append(
            AlphaFoldModel(
                uniprot_accession=uniprot_id,
                model_identifier=(
                    item.get("modelEntityId")
                    or item.get("modelIdentifier")
                    or item.get("entryId")
                )
                if isinstance(
                    item.get("modelEntityId") or item.get("modelIdentifier") or item.get("entryId"),
                    str,
                )
                else None,
                model_url=(
                    item.get("modelUrl")
                    or item.get("url")
                    or f"https://alphafold.ebi.ac.uk/entry/{uniprot_id}"
                ),
                gene=item.get("gene") if isinstance(item.get("gene"), str) else None,
                organism=item.get("organismScientificName") if isinstance(item.get("organismScientificName"), str) else None,
                sequence_length=(
                    item.get("sequenceLength")
                    if isinstance(item.get("sequenceLength"), int)
                    else (len(item["sequence"]) if isinstance(item.get("sequence"), str) else None)
                ),
                confidence_avg=float(item["globalMetricValue"])
                if isinstance(item.get("globalMetricValue"), (int, float))
                else (float(item["confidenceAvg"]) if isinstance(item.get("confidenceAvg"), (int, float)) else None),
            )
        )
    retrieved_at = _retrieved_at()
    output = AlphaFoldLookupOutput(
        uniprot_accession=accession,
        models=models,
        source="alphafold-ebi",
        retrieved_at=retrieved_at,
    ).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_alphafold(
            uniprot_accession=accession,
            models=[model.model_dump(mode="python") for model in models],
            retrieved_at=retrieved_at,
        ),
    )

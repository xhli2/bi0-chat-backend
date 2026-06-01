from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.services.bio_evidence import attach_evidence, evidence_from_ncbi, evidence_from_uniprot
from app.tools.schemas import ToolExecutionError


class NCBISearchInput(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    db: str = Field(default="pubmed")
    retmax: int = Field(default=10, ge=1, le=20)
    sort: str = Field(default="relevance")


class NCBISearchOutput(BaseModel):
    db: str
    term: str
    total_count: int
    ids: list[str]
    source: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


class UniProtLookupInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    organism_id: int | None = Field(default=None, ge=1)
    size: int = Field(default=10, ge=1, le=25)


class UniProtRecord(BaseModel):
    accession: str
    entry_id: str
    protein_name: str | None = None
    gene_names: list[str] = []
    organism_name: str | None = None
    length: int | None = None


class UniProtLookupOutput(BaseModel):
    query: str
    records: list[UniProtRecord]
    source: str
    retrieved_at: str
    evidence: dict | None = None
    evidence_pack: dict | None = None


async def tool_bio_ncbi_search(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = _
    payload = NCBISearchInput.model_validate(args)
    params = {
        "db": payload.db,
        "term": payload.term,
        "retmax": payload.retmax,
        "sort": payload.sort,
        "retmode": "json",
    }
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolExecutionError("NCBI_UPSTREAM_ERROR", f"NCBI request failed: {exc}", retryable=True) from exc
    data = response.json()
    esearch = data.get("esearchresult", {})
    ids = [item for item in esearch.get("idlist", []) if isinstance(item, str)]
    try:
        count = int(esearch.get("count", len(ids)))
    except (TypeError, ValueError):
        count = len(ids)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    output = NCBISearchOutput(
        db=payload.db,
        term=payload.term,
        total_count=count,
        ids=ids,
        source="ncbi-esearch",
        retrieved_at=retrieved_at,
    ).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_ncbi(term=payload.term, db=payload.db, ids=ids, retrieved_at=retrieved_at),
    )


async def tool_bio_uniprot_lookup(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    _ = _
    payload = UniProtLookupInput.model_validate(args)
    query = payload.query
    if payload.organism_id is not None:
        query = f"({query}) AND (organism_id:{payload.organism_id})"
    params = {
        "query": query,
        "size": payload.size,
        "format": "json",
        "fields": "accession,id,protein_name,gene_names,organism_name,length",
    }
    url = "https://rest.uniprot.org/uniprotkb/search"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolExecutionError("UNIPROT_UPSTREAM_ERROR", f"UniProt request failed: {exc}", retryable=True) from exc
    body = response.json()
    records: list[UniProtRecord] = []
    for item in body.get("results", []):
        if not isinstance(item, dict):
            continue
        genes = item.get("genes", [])
        gene_names: list[str] = []
        if isinstance(genes, list):
            for gene in genes:
                if not isinstance(gene, dict):
                    continue
                primary = gene.get("geneName", {})
                if isinstance(primary, dict) and isinstance(primary.get("value"), str):
                    gene_names.append(primary["value"])
        protein_name = None
        protein_desc = item.get("proteinDescription", {})
        if isinstance(protein_desc, dict):
            rec_name = protein_desc.get("recommendedName", {})
            if isinstance(rec_name, dict):
                full_name = rec_name.get("fullName", {})
                if isinstance(full_name, dict) and isinstance(full_name.get("value"), str):
                    protein_name = full_name["value"]
        organism_name = None
        organism = item.get("organism", {})
        if isinstance(organism, dict) and isinstance(organism.get("scientificName"), str):
            organism_name = organism["scientificName"]
        accession = item.get("primaryAccession")
        entry_id = item.get("uniProtkbId")
        if not isinstance(accession, str) or not isinstance(entry_id, str):
            continue
        length_val = item.get("sequence", {}).get("length") if isinstance(item.get("sequence"), dict) else None
        records.append(
            UniProtRecord(
                accession=accession,
                entry_id=entry_id,
                protein_name=protein_name,
                gene_names=gene_names[:5],
                organism_name=organism_name,
                length=length_val if isinstance(length_val, int) else None,
            )
        )
    retrieved_at = datetime.now(timezone.utc).isoformat()
    records_dump = [record.model_dump(mode="python") for record in records]
    output = UniProtLookupOutput(
        query=payload.query,
        records=records,
        source="uniprot-rest",
        retrieved_at=retrieved_at,
    ).model_dump(mode="python")
    return attach_evidence(
        output,
        evidence_from_uniprot(query=payload.query, records=records_dump, retrieved_at=retrieved_at),
    )

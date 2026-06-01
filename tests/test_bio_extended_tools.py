import pytest

from app.tools.adapters.bio_extended import (
    tool_bio_alphafold_lookup,
    tool_bio_ensembl_gene_lookup,
    tool_bio_ensembl_vep,
    tool_bio_mygene_query,
    tool_bio_pdb_search,
)


class _MockResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _MockAsyncClient:
    def __init__(self, *, get_data=None, post_data=None):
        self._get_data = get_data
        self._post_data = post_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params=None, headers=None):
        _ = url, params, headers
        return _MockResponse(self._get_data)

    async def post(self, url: str, json=None, headers=None):
        _ = url, json, headers
        return _MockResponse(self._post_data)


@pytest.mark.asyncio
async def test_mygene_query_normalizes_hits(monkeypatch):
    sample = {
        "total": 1,
        "hits": [
            {
                "symbol": "BRCA1",
                "name": "BRCA1 DNA repair associated",
                "entrezgene": 672,
                "ensembl": {"gene": "ENSG00000012048"},
                "type_of_gene": "protein-coding",
                "summary": "Breast cancer gene",
            }
        ],
    }

    def _factory(*args, **kwargs):
        _ = args, kwargs
        return _MockAsyncClient(get_data=sample)

    monkeypatch.setattr("app.tools.adapters.bio_extended.httpx.AsyncClient", _factory)
    result = await tool_bio_mygene_query({"query": "symbol:BRCA1", "size": 1}, {})
    assert result["total"] == 1
    assert result["hits"][0]["symbol"] == "BRCA1"
    assert result["hits"][0]["ensembl_gene"] == "ENSG00000012048"
    assert result["evidence"]["source"] == "mygene"


@pytest.mark.asyncio
async def test_ensembl_gene_lookup(monkeypatch):
    sample = {
        "id": "ENSG00000012048",
        "display_name": "BRCA1",
        "biotype": "protein_coding",
        "description": "BRCA1 DNA repair associated",
        "assembly_name": "GRCh38",
        "seq_region_name": "17",
        "start": 43044295,
        "end": 43125483,
        "strand": -1,
    }

    def _factory(*args, **kwargs):
        _ = args, kwargs
        return _MockAsyncClient(get_data=sample)

    monkeypatch.setattr("app.tools.adapters.bio_extended.httpx.AsyncClient", _factory)
    result = await tool_bio_ensembl_gene_lookup({"symbol": "BRCA1"}, {})
    assert len(result["records"]) == 1
    assert result["records"][0]["id"] == "ENSG00000012048"
    assert result["records"][0]["seq_region_name"] == "17"


@pytest.mark.asyncio
async def test_ensembl_vep_parses_consequences(monkeypatch):
    sample = [
        {
            "assembly_name": "GRCh38",
            "transcript_consequences": [
                {
                    "consequence_terms": ["missense_variant"],
                    "gene_symbol": "BRCA1",
                    "transcript_id": "ENST00000357654",
                    "impact": "MODERATE",
                    "amino_acids": "C/R",
                    "codons": "Tgc/Cgc",
                }
            ],
        }
    ]

    def _factory(*args, **kwargs):
        _ = args, kwargs
        return _MockAsyncClient(post_data=sample)

    monkeypatch.setattr("app.tools.adapters.bio_extended.httpx.AsyncClient", _factory)
    result = await tool_bio_ensembl_vep({"variant_hgvs": "9:g.22125504G>C"}, {})
    assert result["assembly_name"] == "GRCh38"
    assert result["consequences"][0]["gene_symbol"] == "BRCA1"
    assert result["consequences"][0]["most_severe_consequence"] == "missense_variant"


@pytest.mark.asyncio
async def test_pdb_search_normalizes_entries(monkeypatch):
    sample = {
        "total_count": 1,
        "result_set": [
            {
                "identifier": "1t15",
                "services": [
                    {
                        "nodes": {
                            "rcsb_entry_info": {"title": "BRCA1 RING domain", "resolution_combined": 2.5},
                            "exptl": [{"method": "X-RAY DIFFRACTION"}],
                            "rcsb_entity_source_organism": [{"ncbi_scientific_name": "Homo sapiens"}],
                        }
                    }
                ],
            }
        ],
    }

    def _factory(*args, **kwargs):
        _ = args, kwargs
        return _MockAsyncClient(post_data=sample)

    monkeypatch.setattr("app.tools.adapters.bio_extended.httpx.AsyncClient", _factory)
    result = await tool_bio_pdb_search({"query": "BRCA1", "rows": 5}, {})
    assert result["total_count"] == 1
    assert result["entries"][0]["pdb_id"] == "1T15"
    assert result["entries"][0]["resolution"] == 2.5


@pytest.mark.asyncio
async def test_alphafold_lookup(monkeypatch):
    sample = [
        {
            "uniprotAccession": "P38398",
            "modelEntityId": "AF-P38398-F1",
            "gene": "BRCA1",
            "organismScientificName": "Homo sapiens",
            "sequence": "ACGT" * 100,
            "globalMetricValue": 88.5,
        }
    ]

    def _factory(*args, **kwargs):
        _ = args, kwargs
        return _MockAsyncClient(get_data=sample)

    monkeypatch.setattr("app.tools.adapters.bio_extended.httpx.AsyncClient", _factory)
    result = await tool_bio_alphafold_lookup({"uniprot_accession": "P38398"}, {})
    assert len(result["models"]) == 1
    assert result["models"][0]["model_identifier"] == "AF-P38398-F1"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mygene_live_query():
    result = await tool_bio_mygene_query({"query": "symbol:BRCA1", "size": 1}, {})
    assert result["hits"]
    assert result["hits"][0]["symbol"] == "BRCA1"

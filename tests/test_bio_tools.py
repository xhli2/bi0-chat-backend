import pytest

from app.tools.adapters.bio_public import tool_bio_ncbi_search, tool_bio_uniprot_lookup


class _MockResponse:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _MockAsyncClient:
    def __init__(self, data: dict):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params: dict):
        _ = url, params
        return _MockResponse(self._data)


@pytest.mark.asyncio
async def test_ncbi_tool_normalizes_output(monkeypatch):
    def _factory(*args, **kwargs):
        _ = args, kwargs
        return _MockAsyncClient({"esearchresult": {"count": "2", "idlist": ["1", "2"]}})

    monkeypatch.setattr("app.tools.adapters.bio_public.httpx.AsyncClient", _factory)
    result = await tool_bio_ncbi_search({"term": "BRCA1", "db": "pubmed", "retmax": 2}, {})
    assert result["db"] == "pubmed"
    assert result["total_count"] == 2
    assert result["ids"] == ["1", "2"]


@pytest.mark.asyncio
async def test_uniprot_tool_normalizes_records(monkeypatch):
    sample = {
        "results": [
            {
                "primaryAccession": "P38398",
                "uniProtkbId": "BRCA1_HUMAN",
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Breast cancer type 1 susceptibility protein"}}},
                "genes": [{"geneName": {"value": "BRCA1"}}],
                "organism": {"scientificName": "Homo sapiens"},
                "sequence": {"length": 1863},
            }
        ]
    }

    def _factory(*args, **kwargs):
        _ = args, kwargs
        return _MockAsyncClient(sample)

    monkeypatch.setattr("app.tools.adapters.bio_public.httpx.AsyncClient", _factory)
    result = await tool_bio_uniprot_lookup({"query": "BRCA1", "size": 1}, {})
    assert len(result["records"]) == 1
    assert result["records"][0]["accession"] == "P38398"
    assert result["records"][0]["entry_id"] == "BRCA1_HUMAN"

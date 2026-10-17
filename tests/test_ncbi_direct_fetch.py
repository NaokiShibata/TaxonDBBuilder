"""Regression tests for v3.3.8 correctness audit — direct-fetch fixes."""
import io
from urllib.error import HTTPError

import pytest
import typer

import taxondbbuilder.ncbi as ncbi


@pytest.fixture
def mock_entrez_direct(monkeypatch):
    """Return a mock setup where direct-fetch (no history) is used."""
    import types

    handle = types.SimpleNamespace()
    handle.closed = False
    handle.close = lambda: setattr(handle, "closed", True)

    def esearch(**kw):
        return handle

    def read(h):
        return {"Count": "4", "WebEnv": None, "QueryKey": None}

    def efetch(**kw):
        return io.StringIO(f"{kw.get('webenv', 'none')}:{kw.get('retstart', 0)}")

    monkeypatch.setattr(ncbi.Entrez, "esearch", esearch)
    monkeypatch.setattr(ncbi.Entrez, "read", read)
    monkeypatch.setattr(ncbi.Entrez, "efetch", efetch)
    return handle


# ---------------------------------------------------------------------------
# Finding 1: _fetch_genbank_chunk_by_ids leaks search_handle on exception
# ---------------------------------------------------------------------------


def test_fetch_by_ids_closes_handle_on_efetch_error(monkeypatch):
    """When _read_efetch_with_retries raises, the search handle must be closed."""
    import taxondbbuilder.ncbi as ncbi_module
    import types

    handle = types.SimpleNamespace()
    handle.closed = False
    handle.close = lambda: setattr(handle, "closed", True)

    def esearch(**kw):
        return handle

    def read(h):
        return {"IdList": ["id1", "id2"]}

    def efetch(**kw):
        raise TimeoutError("efetch failed")

    monkeypatch.setattr(ncbi_module.Entrez, "esearch", esearch)
    monkeypatch.setattr(ncbi_module.Entrez, "read", read)
    monkeypatch.setattr(ncbi_module.Entrez, "efetch", efetch)

    ctx = types.SimpleNamespace(
        db="nucleotide",
        query="test query",
        rettype="gb",
        retmode="text",
        per_query=2,
        fetch_retries=1,
        retry_delay_sec=0.0,
    )

    with pytest.raises(TimeoutError, match="efetch failed"):
        ncbi_module._fetch_genbank_chunk_by_ids(ctx, 0)

    assert handle.closed, "search_handle must be closed when efetch raises"


def test_fetch_by_ids_closes_handle_on_empty_ids(monkeypatch):
    """Even when the search returns no IDs, the handle is closed."""
    import taxondbbuilder.ncbi as ncbi_module
    import types

    handle = types.SimpleNamespace()
    handle.closed = False
    handle.close = lambda: setattr(handle, "closed", True)

    def esearch(**kw):
        return handle

    def read(h):
        return {"IdList": []}

    monkeypatch.setattr(ncbi_module.Entrez, "esearch", esearch)
    monkeypatch.setattr(ncbi_module.Entrez, "read", read)

    ctx = types.SimpleNamespace(
        db="nucleotide", query="test", rettype="gb", retmode="text", per_query=2, fetch_retries=0, retry_delay_sec=0.0
    )
    result = ncbi_module._fetch_genbank_chunk_by_ids(ctx, 0)
    assert result is None
    assert handle.closed, "search_handle must be closed on empty result too"


# ---------------------------------------------------------------------------
# Finding 2: _iter_genbank_direct_chunks must catch HTTPError(400)
# ---------------------------------------------------------------------------


def test_direct_fetch_bad_request_raises_bad_parameter(monkeypatch):
    """When direct fetch gets HTTP 400, it raises BadParameter, not unhandled HTTPError."""
    import taxondbbuilder.ncbi as ncbi_module
    import types

    handle = types.SimpleNamespace()
    handle.closed = False
    handle.close = lambda: setattr(handle, "closed", True)

    def esearch(**kw):
        return handle

    def read(h):
        return {"IdList": ["id1"]}

    def efetch_bad(**kw):
        raise HTTPError("https://eutils.ncbi.nlm.nih.gov", 400, "bad", {}, None)

    monkeypatch.setattr(ncbi_module.Entrez, "esearch", esearch)
    monkeypatch.setattr(ncbi_module.Entrez, "read", read)
    monkeypatch.setattr(ncbi_module.Entrez, "efetch", efetch_bad)

    ctx = types.SimpleNamespace(
        db="nucleotide",
        query="test query",
        rettype="gb",
        retmode="text",
        start_at=0,
        count=2,
        per_query=1,
        use_history=False,  # forces _iter_genbank_direct_chunks
        webenv=None,
        query_key=None,
        resume=False,
        snapshot=None,
        resumed_snapshot=False,
        cache_root=None,
        delay_sec=0.0,
        fetch_retries=1,
        retry_delay_sec=0.0,
    )

    chunks = ncbi_module._iter_genbank_direct_chunks(ctx)
    with pytest.raises(typer.BadParameter, match="400"):
        next(chunks)


def test_direct_fetch_non_400_httperror_propagates(monkeypatch):
    """Non-BAD_REQUEST HTTP errors must still propagate."""
    import taxondbbuilder.ncbi as ncbi_module
    import types
    from http import HTTPStatus

    handle = types.SimpleNamespace()
    handle.close = lambda: None

    def esearch(**kw):
        return handle

    def read(h):
        return {"IdList": ["id1"]}

    def efetch_503(**kw):
        raise HTTPError("https://eutils.ncbi.nlm.nih.gov", 503, "server error", {}, None)

    monkeypatch.setattr(ncbi_module.Entrez, "esearch", esearch)
    monkeypatch.setattr(ncbi_module.Entrez, "read", read)
    monkeypatch.setattr(ncbi_module.Entrez, "efetch", efetch_503)

    ctx = types.SimpleNamespace(
        db="nucleotide",
        query="test query",
        rettype="gb",
        retmode="text",
        start_at=0,
        count=2,
        per_query=1,
        use_history=False,
        webenv=None,
        query_key=None,
        resume=False,
        snapshot=None,
        resumed_snapshot=False,
        cache_root=None,
        delay_sec=0.0,
        fetch_retries=1,
        retry_delay_sec=0.0,
    )

    chunks = ncbi_module._iter_genbank_direct_chunks(ctx)
    with pytest.raises(HTTPError) as exc_info:
        next(chunks)
    assert exc_info.value.code == HTTPStatus.SERVICE_UNAVAILABLE

"""Regression coverage for the 3.3.8 correctness audit."""
import pytest
import typer

@pytest.fixture
def cached_ncbi(tmp_path, monkeypatch):
    import taxondbbuilder.ncbi as ncbi
    import io

    calls = []
    monkeypatch.setattr(ncbi.Entrez, "esearch", lambda **kw: io.StringIO())
    monkeypatch.setattr(ncbi.Entrez, "read", lambda handle: {"Count": "4", "WebEnv": "original", "QueryKey": "1"})
    def fetch(**kw):
        calls.append(kw)
        return io.StringIO(f"{kw['webenv']}:{kw['retstart']}")
    monkeypatch.setattr(ncbi.Entrez, "efetch", fetch)
    cfg = dict(rettype="gb", per_query=2, fetch_retries=1)
    _, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path)
    assert next(iter(chunks)) == (0, "original:0")
    def new_search(**kw):
        raise AssertionError("resume must not start a new search")
    monkeypatch.setattr(ncbi.Entrez, "esearch", new_search)
    return ncbi, cfg, calls


def test_resume_uses_original_history_not_new_search(tmp_path, cached_ncbi):
    ncbi, cfg, calls = cached_ncbi
    count, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)
    assert count == 4
    assert list(chunks) == [(0, "original:0"), (2, "original:2")]
    assert [call["retstart"] for call in calls] == [0, 2]


def test_expired_resume_history_never_falls_back_to_new_ids(tmp_path, cached_ncbi, monkeypatch):
    from urllib.error import HTTPError
    ncbi, cfg, _ = cached_ncbi
    def expired(**kw):
        raise HTTPError("https://example.invalid", 400, "expired", {}, None)
    monkeypatch.setattr(ncbi.Entrez, "efetch", expired)
    _, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)
    with pytest.raises(typer.BadParameter, match="snapshot"):
        list(chunks)


def test_resume_rejects_corrupted_page(tmp_path, cached_ncbi):
    ncbi, cfg, _ = cached_ncbi
    next(tmp_path.glob(".cache/query-*/start*.cache")).write_text("corrupted")
    _, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)
    with pytest.raises(typer.BadParameter, match="checksum"):
        list(chunks)


def test_complete_snapshot_resumes_without_network(tmp_path, cached_ncbi, monkeypatch):
    ncbi, cfg, calls = cached_ncbi
    _, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)
    assert len(list(chunks)) == 2
    calls.clear()
    _, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)
    assert len(list(chunks)) == 2
    assert calls == []


def test_legacy_cache_is_preserved_and_rejected(tmp_path):
    import taxondbbuilder.ncbi as ncbi
    from hashlib import sha256

    key = sha256("\0".join(["query", "nucleotide", "gb", "text", "2"]).encode()).hexdigest()[:16]
    cache = tmp_path / ".cache" / f"query-{key}"
    cache.mkdir(parents=True)
    page = cache / "start000000000_count0002.cache"
    page.write_text("legacy data")
    with pytest.raises(typer.BadParameter, match="no search snapshot"):
        ncbi.fetch_genbank("query", dict(rettype="gb", per_query=2), 0, dump_dir=tmp_path, resume=True)
    assert page.read_text() == "legacy data"


@pytest.mark.parametrize("contents", ["{", "[]", '{"schema_version":1}',
                                     '{"schema_version":1,"Count":4,"pages":{},"WebEnv":42}'])
def test_invalid_snapshot_is_rejected(tmp_path, cached_ncbi, contents):
    ncbi, cfg, _ = cached_ncbi
    next(tmp_path.glob(".cache/query-*/snapshot.json")).write_text(contents)
    with pytest.raises(typer.BadParameter, match="Invalid NCBI cache snapshot"):
        ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)


def test_fresh_download_does_not_reuse_stale_pages(tmp_path, cached_ncbi, monkeypatch):
    ncbi, cfg, _ = cached_ncbi
    import io
    monkeypatch.setattr(ncbi.Entrez, "esearch", lambda **kw: io.StringIO())
    monkeypatch.setattr(ncbi.Entrez, "read", lambda h: {"Count": "4", "WebEnv": "new", "QueryKey": "1"})
    _, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path)
    chunks.close()
    _, resumed = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)
    assert list(resumed) == [(0, "new:0"), (2, "new:2")]


def test_incomplete_direct_snapshot_is_not_mixed_with_new_search(tmp_path, cached_ncbi):
    ncbi, cfg, _ = cached_ncbi
    cfg["use_history"] = False
    _, chunks = ncbi.fetch_genbank("query", cfg, 0, dump_dir=tmp_path, resume=True)
    with pytest.raises(typer.BadParameter, match="without history"):
        list(chunks)

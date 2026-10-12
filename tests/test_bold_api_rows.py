"""Characterization of the BOLD API payload and row helpers.

These pin the shapes the BOLD JSON/TSV responses actually arrive in, including
the two transport details that are easy to get wrong: a term with no scope or
value contributes nothing to the query, and a gzip transfer has no usable total.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from .test_bold_and_primer import _BoldResponse


def test_successful_terms_cover_string_and_dict_shapes():
    import taxondb_bold as bold

    payload = {
        "successful_terms": [
            "plain",
            "  spaced  ",
            {"matched": " matched-term "},
            {"scope": "COI", "field": "markercode", "value": "COI-5P"},
            {"scope": "COI", "value": "COI-5P"},
            {"scope": "S", "field": "  ", "value": "V"},
            {"scope": "", "value": ""},
            {"scope": "COI", "value": "  "},
            42,
            None,
        ]
    }
    assert bold._extract_successful_terms(payload) == [
        "plain",
        "spaced",
        "matched-term",
        "COI:markercode:COI-5P",
        "COI:COI-5P",
        "S:V",
    ]
    assert bold._extract_successful_terms({"successful_terms": "no"}) == []
    assert bold._extract_successful_terms(None) == []


def test_first_scalar_searches_nested_keys_case_insensitively():
    import taxondb_bold as bold

    assert bold._first_scalar({"a": {"b": ["", " hit "]}}, ["B"]) == "hit"
    assert bold._first_scalar({"x": {"y": 3}}, ["Y"]) == "3"
    assert bold._first_scalar({"x": [1, 2]}, ["X"]) == "1"
    assert bold._first_scalar({}, ["X"]) is None


def test_document_rows_accept_lists_wrapped_dicts_and_single_records():
    import taxondb_bold as bold

    assert bold.extract_document_rows([{"a": 1}, "x", [1]]) == [{"a": 1}]
    for key in ("documents", "records", "items", "data", "results"):
        assert bold.extract_document_rows({key: [{"m": 1}, "z"]}) == [{"m": 1}]
    assert bold.extract_document_rows({"processid": "P1"}) == [{"processid": "P1"}]
    assert bold.extract_document_rows({"other": "x"}) == []
    assert bold.extract_document_rows("str") == []


def test_tsv_rows_drop_blank_columns_and_valueless_rows(tmp_path: Path):
    import taxondb_bold as bold

    path = tmp_path / "rows.tsv"
    path.write_text(
        "\ufeffmarker_code\tnucleotides\t\n"
        "COI-5P\tAA\t\n"
        "\t\t\n"
        "   \t \t\n"
        "X\t\t\n",
        encoding="utf-8",
    )
    assert list(bold.iter_tsv_document_rows(path)) == [
        {"marker_code": "COI-5P", "nucleotides": "AA"},
        {"marker_code": "X", "nucleotides": ""},
    ]


def test_marker_resolution_prefers_exact_codes_then_looser_strategies():
    import taxondb_bold as bold

    marker_map = {
        "coi": {
            "bold": {"marker_codes": ["COI-5P"]},
            "aliases": ["coi"],
            "phrases": ["COI"],
        },
        "16s": {},
    }
    assert bold.resolve_marker_from_record("COI-5P", ["coi"], marker_map) == (
        "coi",
        "COI-5P",
    )
    # Not an exact code, so only the alias strategy can reach it.
    assert bold.resolve_marker_from_record("COI-5P-like", ["coi"], marker_map) == (
        "coi",
        "COI-5P-like",
    )
    assert bold.resolve_marker_from_record("16S ribosome", ["16s"], marker_map) == (
        "16s",
        "16S ribosome",
    )
    assert bold.resolve_marker_from_record("", ["16s"], marker_map) is None
    assert bold.resolve_marker_from_record(None, ["16s"], marker_map) is None
    assert (
        bold.resolve_marker_from_record("x", ["coi"], {"coi": {"aliases": ["", "  ", 5, "x"]}})
        == ("coi", "x")
    )


def test_download_streams_gzip_body_without_a_misleading_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondb_bold as bold

    body = gzip.compress(b"marker_code\nCOI-5P\n")
    monkeypatch.setattr(
        bold,
        "urlopen",
        lambda *args, **kwargs: _BoldResponse(body, encoding="gzip", content_length="39"),
    )
    dest = tmp_path / "documents.tsv"
    meta = bold.download_documents_to_path("qid", bold.get_bold_runtime_config({}), dest)
    assert meta["downloaded_bytes"] == len(b"marker_code\nCOI-5P\n")
    # 39 is the compressed transfer size; reporting it as the total would let
    # progress pass 100%.
    assert meta["content_length"] is None
    assert dest.read_bytes() == b"marker_code\nCOI-5P\n"
    assert [path.name for path in tmp_path.iterdir()] == ["documents.tsv"]


def test_download_reports_http_error_and_leaves_no_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondb_bold as bold

    def boom(*args, **kwargs):
        raise HTTPError("http://x", 404, "nope", {}, None)

    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    dest = tmp_path / "documents.tsv"
    with pytest.raises(bold.BoldApiError, match="BOLD HTTP error 404: nope"):
        bold.download_documents_to_path(
            "qid",
            bold.get_bold_runtime_config({"retries": 1, "backoff_sec": 0}),
            dest,
        )
    assert [path.name for path in tmp_path.iterdir()] == []
    with pytest.raises(bold.BoldApiError, match="Unsupported BOLD download format"):
        bold.download_documents_to_path(
            "qid", bold.get_bold_runtime_config({}), dest, fmt="bogus"
        )


def test_fetch_bold_documents_returns_a_real_path_and_reuses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import logging
    from types import SimpleNamespace

    import taxondb_bold as bold
    from taxondbbuilder.bold import _fetch_bold_documents

    payload = b"marker_code\nCOI-5P\n"
    monkeypatch.setattr(bold, "urlopen", lambda *args, **kwargs: _BoldResponse(payload))
    prepared = SimpleNamespace(
        query_id="qid",
        runtime_cfg=bold.get_bold_runtime_config({}),
        download_format="tsv",
    )
    dest = tmp_path / "cache" / "query-abc.tsv"
    logger = logging.Logger("fetch")

    meta = _fetch_bold_documents(
        prepared, dest, "tsv", "Taxon", False, logger, None
    )
    # The metadata path must be the file that actually exists, and no .part may
    # survive; the caller used to rename a second time behind download's back.
    assert Path(meta["path"]) == dest
    assert dest.read_bytes() == payload
    assert [path.name for path in dest.parent.iterdir()] == ["query-abc.tsv"]

    cached = _fetch_bold_documents(prepared, dest, "tsv", "Taxon", True, logger, None)
    assert cached["cached"] is True
    assert cached["downloaded_bytes"] == len(payload)


def test_request_json_retries_network_error_with_linear_backoff(
    monkeypatch: pytest.MonkeyPatch
):
    """retries+1 attempts, delay = backoff_sec * (attempt+1), cause preserved."""
    import taxondb_bold as bold

    calls = 0

    def boom(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise URLError("down")

    sleeps: list[float] = []
    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    cfg = bold.get_bold_runtime_config({"retries": 2, "backoff_sec": 1.5})
    with pytest.raises(bold.BoldApiError, match="BOLD network error") as excinfo:
        bold._request_json("https://example.invalid", cfg)
    assert calls == 3
    assert sleeps == [1.5, 3.0]
    assert isinstance(excinfo.value.__cause__, URLError)


def test_request_json_never_retries_a_json_decode_error(
    monkeypatch: pytest.MonkeyPatch
):
    """A malformed body is not transient: one attempt, no sleep."""
    import taxondb_bold as bold

    calls = 0

    def not_json(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _BoldResponse(b"not-json")

    sleeps: list[float] = []
    monkeypatch.setattr(bold, "urlopen", not_json)
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    cfg = bold.get_bold_runtime_config({"retries": 2, "backoff_sec": 1.5})
    with pytest.raises(bold.BoldApiError, match="JSON decode error"):
        bold._request_json("https://example.invalid", cfg)
    assert calls == 1
    assert sleeps == []


def test_request_json_reports_http_status_and_reason(
    monkeypatch: pytest.MonkeyPatch
):
    import taxondb_bold as bold

    def boom(*args, **kwargs):
        raise HTTPError("http://x", 503, "unavailable", {}, None)

    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    cfg = bold.get_bold_runtime_config({"retries": 0})
    with pytest.raises(bold.BoldApiError, match="BOLD HTTP error 503: unavailable") as excinfo:
        bold._request_json("https://example.invalid", cfg)
    assert isinstance(excinfo.value.__cause__, HTTPError)


def test_download_retries_http_error_with_linear_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondb_bold as bold

    def boom(*args, **kwargs):
        raise HTTPError("http://x", 503, "unavailable", {}, None)

    sleeps: list[float] = []
    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    cfg = bold.get_bold_runtime_config({"retries": 2, "backoff_sec": 1.5})
    dest = tmp_path / "documents.tsv"
    with pytest.raises(bold.BoldApiError, match="BOLD HTTP error 503: unavailable") as excinfo:
        bold.download_documents_to_path("qid", cfg, dest)
    assert sleeps == [1.5, 3.0]
    assert isinstance(excinfo.value.__cause__, HTTPError)
    assert [path.name for path in tmp_path.iterdir()] == []


def test_download_leaves_no_part_file_when_the_body_fails_midstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The .part cleanup runs even when the failure is in read(), not urlopen."""
    import taxondb_bold as bold

    class _BrokenBody(_BoldResponse):
        def read(self, size: int = -1) -> bytes:
            raise OSError("connection reset")

    monkeypatch.setattr(bold, "urlopen", lambda *args, **kwargs: _BrokenBody(b"x"))
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    cfg = bold.get_bold_runtime_config({"retries": 1, "backoff_sec": 0})
    dest = tmp_path / "documents.tsv"
    with pytest.raises(bold.BoldApiError, match="BOLD download error"):
        bold.download_documents_to_path("qid", cfg, dest)
    assert [path.name for path in tmp_path.iterdir()] == []

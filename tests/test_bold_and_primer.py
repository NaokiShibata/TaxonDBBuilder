from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from .conftest import json_text, read_golden


def test_bold_row_normalization_and_canonical_record(golden_dir: Path):
    import taxondbbuilder as builder
    from taxondb_bold import normalize_bold_row, parse_accession_tokens

    marker_map = builder.normalize_marker_map(
        {
            "coi": {
                "aliases": ["coi"],
                "phrases": ["COI"],
                "bold": {"marker_codes": ["COI-5P", "COI"]},
            }
        },
        builder.BuildSource.BOTH,
    )
    raw_row = {
        "marker_code": "COI-5P",
        "nucleotides": "acgt- 12!",
        "processid": "PRC001",
        "sampleid": "SAM001",
        "insdc_acs": "AB123, AB124;AB123",
        "species": "Fish species",
    }
    normalized = normalize_bold_row(raw_row, ["coi"], marker_map)
    assert normalized is not None
    record = builder.build_bold_canonical_record(
        normalized,
        marker_map,
        {"header_formats": {"verbose": "{db}|{acc_id}|{organism_raw}|{marker_raw}"}},
    )
    assert parse_accession_tokens(normalized["accession"]) == ["AB123", "AB124"]
    assert json_text(builder.canonical_record_to_dict(record)) == read_golden(
        golden_dir, "bold-record.json"
    )
    assert (
        normalize_bold_row(
            {"marker_code": "unknown", "nucleotides": "ACGT"}, ["coi"], marker_map
        )
        is None
    )


def test_primer_pure_functions_are_characterized():
    import taxondbbuilder as builder

    assert builder.required_overlap_bp(10, None, 0.55) == 6
    assert builder.required_overlap_bp(10, 8, 0.55) == 8
    assert builder.count_mismatches("ACGT", "ANNT") == 0
    assert builder.count_mismatches("ACGT", "AAAA") == 3
    prefix = builder.find_best_prefix_match("TTACGTAA", ["ACGT"], 0, 0.0, None, 1.0, 2)
    suffix = builder.find_best_suffix_match("GGACGT", ["ACGT"], 0, 0.0, None, 1.0, 0)
    assert prefix is not None and suffix is not None
    assert (prefix.overlap_bp, prefix.trim_bp, prefix.full_len_match) == (4, 6, True)
    assert (suffix.overlap_bp, suffix.trim_bp, suffix.full_len_match) == (4, 4, True)
    canonical = builder.OrientationScore("canonical", prefix, suffix)
    reverse = builder.OrientationScore("reverse", None, suffix)
    assert builder.resolve_orientation("ignored", canonical, reverse) == (
        canonical,
        False,
    )
    assert builder.resolve_orientation("ignored", canonical, canonical) == (
        canonical,
        True,
    )
    assert [
        builder.confidence_label(*args)
        for args in [
            (2, 0, False),
            (2, 2, False),
            (1, 0, False),
            (0, 0, False),
            (2, 0, True),
        ]
    ] == ["high", "medium", "medium", "low", "medium"]
    row = {"left_hit": 1, "left_trim_bp": 5, "right_hit": 0, "right_overlap_bp": 4}
    assert builder.compute_trim_lengths_from_row(
        row, builder.PRIMER_TRIM_MODE_ONE_OR_BOTH
    ) == (5, 0)
    assert builder.compute_trim_lengths_from_row(
        row, builder.PRIMER_TRIM_MODE_BOTH_REQUIRED
    ) == (0, 0)


def test_primer_normalization_and_combination():
    import taxondbbuilder as builder

    data = {
        "alpha": {"forward": ["ACGT"], "reverse": ["TGCA"]},
        "beta": {"forward": ["RGTU"], "reverse": ["CARY"]},
    }
    assert builder.normalize_primer_values(["rgu"], "fixture", "forward") == ["RGT"]
    assert builder.combine_primer_set_sequences(data, ["alpha", "beta"]) == (
        ["ACGT", "RGTU"],
        ["TGCA", "CARY"],
    )


class _BoldHeaders(dict):
    def get_content_charset(self):
        return "utf-8"


class _BoldResponse:
    def __init__(
        self, body: bytes, *, encoding: str = "", content_length: str | None = None
    ):
        self._stream = io.BytesIO(body)
        self.headers = _BoldHeaders(
            {
                "Content-Encoding": encoding,
                "Content-Length": content_length or str(len(body)),
            }
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def get_content_charset(self):
        return "utf-8"


def test_bold_runtime_config_and_payload_parsing():
    import taxondb_bold as bold

    cfg = bold.get_bold_runtime_config(
        {"base_url": "https://example.invalid/", "download_format": "JSON"}
    )
    assert cfg["base_url"] == "https://example.invalid"
    assert cfg["download_format"] == "json"
    assert bold.build_taxon_query("Testus alpha") == "tax:Testus alpha"
    assert (
        bold._build_url("https://example.invalid", "/query", {"q": "a b"})
        == "https://example.invalid/query?q=a+b"
    )
    assert bold._parse_json_payload('\ufeff{"a": 1}') == {"a": 1}
    assert bold._parse_json_payload('{"a": 1} {"b": 2}') == [{"a": 1}, {"b": 2}]
    assert bold._parse_json_payload('{"a": 1}\n{"b": 2}') == [{"a": 1}, {"b": 2}]
    with pytest.raises(json.JSONDecodeError):
        bold._parse_json_payload("   ")

    invalid = [
        ({"timeout_sec": 0}, "timeout_sec"),
        ({"retries": -1}, "retries"),
        ({"backoff_sec": -1}, "backoff_sec"),
        ({"download_format": "xml"}, "download_format"),
        ({"download_chunk_size": 0}, "download_chunk_size"),
    ]
    for options, message in invalid:
        with pytest.raises(bold.BoldApiError, match=message):
            bold.get_bold_runtime_config(options)


def test_bold_request_json_retries_gzip_and_errors(monkeypatch: pytest.MonkeyPatch):
    import taxondb_bold as bold

    cfg = bold.get_bold_runtime_config({"retries": 1, "backoff_sec": 1})
    calls = {"count": 0}
    compressed = gzip.compress(b'{"ok": true}')

    def retry_then_success(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise URLError("temporary")
        return _BoldResponse(compressed, encoding="gzip")

    monkeypatch.setattr(bold, "urlopen", retry_then_success)
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    assert bold._request_json("https://example.invalid", cfg) == {"ok": True}
    assert calls["count"] == 2

    def http_error(*args, **kwargs):
        raise HTTPError("https://example.invalid", 500, "broken", {}, None)

    monkeypatch.setattr(bold, "urlopen", http_error)
    with pytest.raises(bold.BoldApiError, match="HTTP error 500"):
        bold._request_json(
            "https://example.invalid", bold.get_bold_runtime_config({"retries": 0})
        )

    monkeypatch.setattr(
        bold, "urlopen", lambda *args, **kwargs: _BoldResponse(b"not-json")
    )
    with pytest.raises(bold.BoldApiError, match="JSON decode error"):
        bold._request_json(
            "https://example.invalid", bold.get_bold_runtime_config({"retries": 0})
        )


def test_bold_query_preparation_and_validation(monkeypatch: pytest.MonkeyPatch):
    import taxondb_bold as bold

    responses = iter(
        [
            {
                "successful_terms": [
                    {"scope": "taxon", "field": "name", "value": "Testus"},
                    "marker",
                ]
            },
            {"counts": {"specimens": [2]}},
            {"queryId": "query 1"},
        ]
    )
    monkeypatch.setattr(
        bold, "_request_json", lambda *_args, **_kwargs: next(responses)
    )
    prepared = bold.prepare_bold_query("Testus alpha", {"backoff_sec": 0})
    assert prepared.normalized_query == "taxon:name:Testus;marker"
    assert prepared.specimen_count == 2
    assert prepared.query_id == "query 1"
    assert prepared.download_format == "tsv"
    monkeypatch.setattr(bold, "_request_json", lambda *_args, **_kwargs: {})
    assert (
        bold.fetch_specimen_count(
            "x",
            {
                "base_url": "x",
                "retries": 0,
                "timeout_sec": 1,
                "backoff_sec": 0,
                "user_agent": "x",
                "download_chunk_size": 1,
            },
        )
        is None
    )

    monkeypatch.setattr(bold, "_request_json", lambda *_args, **_kwargs: {"count": "0"})
    assert bold.prepare_bold_query("Testus alpha", {"backoff_sec": 0}).query_id is None

    monkeypatch.setattr(
        bold, "_request_json", lambda *_args, **_kwargs: {"count": 2_000_000}
    )
    with pytest.raises(bold.BoldApiError, match="exceeds maximum"):
        bold.prepare_bold_query("Testus alpha", {"backoff_sec": 0})

    monkeypatch.setattr(bold, "_request_json", lambda *_args, **_kwargs: {})
    with pytest.raises(bold.BoldApiError, match="query_id"):
        bold.submit_query(
            "x",
            {
                "base_url": "x",
                "retries": 0,
                "timeout_sec": 1,
                "backoff_sec": 0,
                "user_agent": "x",
                "download_chunk_size": 1,
            },
        )


def test_bold_download_and_document_row_iterators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondb_bold as bold

    payload = b"marker_code\tnucleotides\nCOI-5P\tAACCGG\n"
    monkeypatch.setattr(
        bold,
        "urlopen",
        lambda *args, **kwargs: _BoldResponse(payload, content_length="bad"),
    )
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    progress: list[tuple[int, int | None]] = []
    destination = tmp_path / "nested" / "documents.tsv"
    runtime_cfg = bold.get_bold_runtime_config(
        {"download_chunk_size": 3, "backoff_sec": 0}
    )
    meta = bold.download_documents_to_path(
        "query/id",
        runtime_cfg,
        destination,
        progress_callback=lambda n, total: progress.append((n, total)),
    )
    assert meta["downloaded_bytes"] == len(payload)
    assert meta["content_length"] is None
    assert progress[-1][0] == len(payload)
    assert (
        list(bold.iter_document_rows_from_path(destination, "tsv"))[0]["marker_code"]
        == "COI-5P"
    )

    json_path = tmp_path / "documents.json"
    json_path.write_text(
        json.dumps({"records": [{"marker_code": "COI-5P", "nucleotides": "AA"}]}),
        encoding="utf-8",
    )
    assert (
        list(bold.iter_document_rows_from_path(json_path, "json"))[0]["nucleotides"]
        == "AA"
    )
    assert bold.extract_document_rows([{"a": 1}, "ignored"]) == [{"a": 1}]
    assert bold.extract_document_rows({"marker_code": "COI-5P", "nucleotides": "AA"})
    assert bold.extract_document_rows({}) == []
    with pytest.raises(bold.BoldApiError, match="Unsupported"):
        list(bold.iter_document_rows_from_path(destination, "xml"))

    monkeypatch.setattr(
        bold,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(bold.BoldApiError, match="network error"):
        bold.download_documents_to_path(
            "query",
            bold.get_bold_runtime_config({"retries": 0, "backoff_sec": 0}),
            destination,
        )


def test_fetch_bold_records_for_taxon_json_and_zero(monkeypatch: pytest.MonkeyPatch):
    import taxondb_bold as bold

    marker_map = {
        "coi": {
            "bold": {"marker_codes": ["COI-5P"]},
            "aliases": ["coi"],
            "phrases": ["COI"],
        }
    }
    prepared = bold.PreparedBoldQuery(
        scientific_name="Testus alpha",
        raw_query="tax:Testus alpha",
        normalized_query="taxon:name:Testus alpha",
        specimen_count=1,
        query_id="query",
        runtime_cfg={"download_format": "json"},
        download_format="json",
    )
    monkeypatch.setattr(bold, "prepare_bold_query", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(
        bold,
        "download_documents",
        lambda *_args, **_kwargs: {
            "documents": [
                {
                    "marker_code": "COI-5P",
                    "nucleotides": "acgt-",
                    "processid": "P1",
                    "species": "Testus alpha",
                }
            ]
        },
    )
    rows, stats = bold.fetch_bold_records_for_taxon("Testus alpha", ["coi"], marker_map)
    assert rows[0]["sequence"] == "ACGT"
    assert stats["downloaded_rows"] == 1
    assert stats["matched_rows"] == 1

    zero = prepared.__class__(
        **{**prepared.__dict__, "specimen_count": 0, "query_id": None}
    )
    monkeypatch.setattr(bold, "prepare_bold_query", lambda *_args, **_kwargs: zero)
    rows, stats = bold.fetch_bold_records_for_taxon("Testus alpha", ["coi"], marker_map)
    assert rows == [] and stats["specimen_count"] == 0

    missing_id = prepared.__class__(**{**prepared.__dict__, "query_id": None})
    monkeypatch.setattr(
        bold, "prepare_bold_query", lambda *_args, **_kwargs: missing_id
    )
    with pytest.raises(bold.BoldApiError, match="query_id"):
        bold.fetch_bold_records_for_taxon("Testus alpha", ["coi"], marker_map)

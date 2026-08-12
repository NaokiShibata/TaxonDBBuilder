from __future__ import annotations

import io
import re
import shutil
from http import HTTPStatus
from http.client import RemoteDisconnected
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
import typer
from typer.testing import CliRunner

from .conftest import json_text, read_golden


def _normalize_log(text: str, root: Path, tmp_path: Path) -> str:
    text = re.sub(
        r"(?m)^│ Config  │ .*\n(?:│         │ .*\n)*",
        "│ Config  │ <CONFIG> │\n",
        text,
    )
    text = text.replace(str(root), "<ROOT>")
    text = _normalize_tmp_paths(text, tmp_path)
    text = re.sub(r"(?m)^(# (?:started|finished)): .*", r"\1: <TIMESTAMP>", text)
    text = re.sub(r"taxondbbuilder_spool_[^/]+", "taxondbbuilder_spool_<ID>", text)
    return text


def _normalize_tmp_paths(text: str, tmp_path: Path) -> str:
    text = text.replace(str(tmp_path), "<TMP>")
    return re.sub(r"/tmp/pytest-of-[^/]+/pytest-\d+/[^ \n]+", "<TMP>", text)


def _assert_golden(
    path: Path,
    golden_dir: Path,
    name: str,
    *,
    root: Path,
    tmp_path: Path,
    log: bool = False,
) -> None:
    actual = path.read_text(encoding="utf-8")
    if log:
        actual = _normalize_log(actual, root, tmp_path)
    expected = read_golden(golden_dir, name)
    assert actual == expected


def _assert_text_golden(
    actual: str, golden_dir: Path, name: str, tmp_path: Path
) -> None:
    actual = _normalize_tmp_paths(actual, tmp_path)
    assert actual == read_golden(golden_dir, name)


def _assert_build_artifacts(
    output_path: Path,
    golden_dir: Path,
    scenario: str,
    *,
    root: Path,
    tmp_path: Path,
    optional: tuple[str, ...] = (),
) -> None:
    suffixes = (
        "",
        ".source_merge.csv",
        ".acc_organism.csv",
        ".log",
        *optional,
    )
    for suffix in suffixes:
        path = output_path.with_name(output_path.name + suffix)
        _assert_golden(
            path,
            golden_dir,
            f"{scenario}{suffix.replace('/', '_')}.golden",
            root=root,
            tmp_path=tmp_path,
            log=suffix == ".log",
        )


def _bold_download_stub(payload: str):
    def download(
        _query_id: str, _runtime_cfg: dict[str, Any], dest_path: Path, **kwargs: Any
    ) -> dict[str, Any]:
        dest_path.write_text(payload, encoding="utf-8")
        return {
            "path": str(dest_path),
            "format": kwargs.get("fmt", "tsv"),
            "downloaded_bytes": len(payload.encode("utf-8")),
            "content_length": len(payload.encode("utf-8")),
        }

    return download


def _stub_prepared_query(builder):
    from taxondb_bold import PreparedBoldQuery

    return PreparedBoldQuery(
        scientific_name="Testus alpha",
        raw_query="tax:Testus alpha",
        normalized_query="Testus alpha",
        specimen_count=3,
        query_id="fixture-query",
        runtime_cfg={"download_format": "tsv"},
        download_format="tsv",
    )


def _invoke_build(runner: CliRunner, builder, args: list[str]):
    return runner.invoke(builder.app, ["build", *args], catch_exceptions=True)


def test_build_ncbi_from_gb_and_dump_matches_golden(
    fixture_dir: Path, golden_dir: Path, tmp_path: Path
):
    import taxondbbuilder as builder

    gb_dir = tmp_path / "ncbi-gb"
    gb_dir.mkdir()
    shutil.copyfile(fixture_dir / "sample.gb", gb_dir / "sample.gb")
    output = tmp_path / "ncbi.fasta"
    result = _invoke_build(
        CliRunner(),
        builder,
        [
            "-c",
            str(fixture_dir / "minimal_config.toml"),
            "-t",
            "999",
            "-m",
            "12s",
            "--from-gb",
            str(gb_dir),
            "--dump-gb",
            str(tmp_path / "dump"),
            "--out",
            str(output),
            "--workers",
            "1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    _assert_build_artifacts(
        output,
        golden_dir,
        "build-ncbi",
        root=Path(__file__).resolve().parents[1],
        tmp_path=tmp_path,
    )
    dump_path = tmp_path / "dump" / "taxid999" / "TEST0001.1.gb"
    assert dump_path.exists()
    assert "ACCESSION   TEST0001" in dump_path.read_text(encoding="utf-8")


def test_build_bold_download_stub_matches_golden(
    fixture_dir: Path, golden_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondbbuilder as builder
    from taxondbbuilder import bold, cli, ncbi

    payload = (
        "marker_code\tnucleotides\tprocessid\tsampleid\tinsdcacs\tspecies\n"
        "COI-5P\tAACCGG\tBOLD001\tS001\tBOLDACC\tTestus alpha\n"
        "unknown\tTTTT\tBOLD002\tS002\t\tTestus beta\n"
    )
    monkeypatch.setattr(
        ncbi, "fetch_taxonomy_scientific_name", lambda _taxid: "Testus alpha"
    )
    monkeypatch.setattr(
        cli,
        "prepare_bold_query",
        lambda *_args, **_kwargs: _stub_prepared_query(builder),
    )
    monkeypatch.setattr(
        bold, "download_documents_to_path", _bold_download_stub(payload)
    )

    output = tmp_path / "bold.fasta"
    result = _invoke_build(
        CliRunner(),
        builder,
        [
            "-c",
            str(fixture_dir / "minimal_config.toml"),
            "-t",
            "999",
            "-m",
            "coi",
            "--source",
            "bold",
            "--out",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.stdout
    _assert_build_artifacts(
        output,
        golden_dir,
        "build-bold",
        root=Path(__file__).resolve().parents[1],
        tmp_path=tmp_path,
    )


def test_build_bold_resume_reuses_cached_download(
    fixture_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondbbuilder as builder
    from taxondbbuilder import bold, cli, ncbi

    payload = (
        "marker_code\tnucleotides\tprocessid\tsampleid\tinsdcacs\tspecies\n"
        "COI-5P\tAACCGG\tBOLD001\tS001\t\tTestus alpha\n"
    )
    downloads = {"count": 0}

    def download(*args: Any, **kwargs: Any) -> dict[str, Any]:
        downloads["count"] += 1
        return _bold_download_stub(payload)(*args, **kwargs)

    monkeypatch.setattr(
        ncbi, "fetch_taxonomy_scientific_name", lambda _taxid: "Testus alpha"
    )
    monkeypatch.setattr(
        cli,
        "prepare_bold_query",
        lambda *_args, **_kwargs: _stub_prepared_query(builder),
    )
    monkeypatch.setattr(bold, "download_documents_to_path", download)
    cache_dir = tmp_path / "cache"
    base_args = [
        "-c",
        str(fixture_dir / "minimal_config.toml"),
        "-t",
        "999",
        "-m",
        "coi",
        "--source",
        "bold",
        "--dump-gb",
        str(cache_dir),
    ]

    first = _invoke_build(
        CliRunner(), builder, [*base_args, "--out", str(tmp_path / "first.fasta")]
    )
    second = _invoke_build(
        CliRunner(),
        builder,
        [*base_args, "--resume", "--out", str(tmp_path / "second.fasta")],
    )

    assert first.exit_code == second.exit_code == 0
    assert downloads["count"] == 1
    assert len(list((cache_dir / ".cache" / "bold").glob("query-*.tsv"))) == 1


def test_build_both_strict_link_suppression_and_unlinked_record(
    fixture_dir: Path, golden_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondbbuilder as builder
    from taxondbbuilder import bold, cli, ncbi

    gb_dir = tmp_path / "both-gb"
    gb_dir.mkdir()
    shutil.copyfile(fixture_dir / "sample.gb", gb_dir / "sample.gb")
    payload = (
        "marker_code\tnucleotides\tprocessid\tsampleid\tinsdcacs\tspecies\n"
        "12S\tGGGG\tBOLD-LINK\tS001\tTEST0001.1\tTestus alpha\n"
        "12S\tCCCC\tBOLD-KEEP\tS002\tUNLINKED\tTestus beta\n"
    )
    monkeypatch.setattr(
        ncbi, "fetch_taxonomy_scientific_name", lambda _taxid: "Testus alpha"
    )
    monkeypatch.setattr(
        cli,
        "prepare_bold_query",
        lambda *_args, **_kwargs: _stub_prepared_query(builder),
    )
    monkeypatch.setattr(
        bold, "download_documents_to_path", _bold_download_stub(payload)
    )

    output = tmp_path / "both.fasta"
    result = _invoke_build(
        CliRunner(),
        builder,
        [
            "-c",
            str(fixture_dir / "minimal_config.toml"),
            "-t",
            "999",
            "-m",
            "12s",
            "--source",
            "both",
            "--from-gb",
            str(gb_dir),
            "--out",
            str(output),
            "--workers",
            "1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    _assert_build_artifacts(
        output,
        golden_dir,
        "build-both",
        root=Path(__file__).resolve().parents[1],
        tmp_path=tmp_path,
    )


def test_build_post_prep_matches_fasta_sidecars_and_duplicate_reports(
    fixture_dir: Path, golden_dir: Path, tmp_path: Path
):
    import taxondbbuilder as builder

    gb_dir = tmp_path / "postprep-gb"
    (gb_dir / "taxid999").mkdir(parents=True)
    shutil.copyfile(fixture_dir / "postprep.gb", gb_dir / "taxid999" / "postprep.gb")
    output = tmp_path / "postprep.fasta"
    result = _invoke_build(
        CliRunner(),
        builder,
        [
            "-c",
            str(fixture_dir / "post_prep_config.toml"),
            "-t",
            "999",
            "-m",
            "12s",
            "--from-gb",
            str(gb_dir),
            "--post-prep",
            "--out",
            str(output),
            "--workers",
            "1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    _assert_build_artifacts(
        output,
        golden_dir,
        "build-postprep",
        root=Path(__file__).resolve().parents[1],
        tmp_path=tmp_path,
        optional=(
            ".postprep.primer.retained.fasta",
            ".postprep.primer.tsv",
            ".duplicate_acc.records.csv",
            ".duplicate_acc.groups.csv",
        ),
    )


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (
            ["--source", "bold", "--from-gb", "tests/fixtures"],
            "not supported with --source bold",
        ),
        (["--source", "bold", "--resume"], "--resume requires --dump-gb"),
        (["--resume"], "--resume requires --dump-gb or --from-gb"),
    ],
)
def test_build_rejects_invalid_cache_options(
    fixture_dir: Path,
    extra_args: list[str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
):
    import taxondbbuilder as builder
    from taxondbbuilder import ncbi

    monkeypatch.setattr(
        ncbi, "fetch_taxonomy_scientific_name", lambda _taxid: "Testus alpha"
    )
    kwargs = {
        "config": fixture_dir / "minimal_config.toml",
        "taxon": ["999"],
        "marker": ["12s"],
        "source": builder.BuildSource.BOLD
        if "--source" in extra_args
        else builder.BuildSource.NCBI,
        "out": None,
        "dump_gb": Path(extra_args[extra_args.index("--dump-gb") + 1])
        if "--dump-gb" in extra_args
        else None,
        "from_gb": Path(extra_args[extra_args.index("--from-gb") + 1])
        if "--from-gb" in extra_args
        else None,
        "resume": "--resume" in extra_args,
        "dry_run": False,
        "workers": 2,
        "output_prefix": "taxondbbuilder_",
        "post_prep": False,
        "post_prep_step": None,
        "post_prep_primer_set": None,
    }
    with pytest.raises(typer.BadParameter, match=re.escape(message)):
        builder.build(**kwargs)


def test_apply_post_prep_primer_trim_matches_fasta_and_tsv_goldens(
    golden_dir: Path, tmp_path: Path
):
    import taxondbbuilder as builder

    fasta = tmp_path / "primer.fasta"
    fasta.write_text(
        ">both\nACGTGGGGTGCA\n"
        ">left\nACGTGG\n"
        ">right\nGGGGTGCA\n"
        ">none\nCCCC\n"
        ">empty\nACGTTGCA\n",
        encoding="utf-8",
    )
    stats = builder.apply_post_prep_primer_trim(
        fasta,
        ["ACGT"],
        ["TGCA"],
        options={"sidecar_format": "tsv", "keep_retained_fasta": True},
    )
    _assert_text_golden(
        json_text(stats), golden_dir, "primer-trim.stats.golden", tmp_path
    )
    _assert_text_golden(
        fasta.read_text(encoding="utf-8"),
        golden_dir,
        "primer-trim.fasta.golden",
        tmp_path,
    )
    _assert_text_golden(
        fasta.with_suffix(".fasta.postprep.primer.tsv").read_text(encoding="utf-8"),
        golden_dir,
        "primer-trim.tsv.golden",
        tmp_path,
    )
    _assert_text_golden(
        fasta.with_suffix(".fasta.postprep.primer.retained.fasta").read_text(
            encoding="utf-8"
        ),
        golden_dir,
        "primer-trim.retained.fasta.golden",
        tmp_path,
    )


def test_apply_post_prep_primer_trim_vsearch_branch_is_stubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondbbuilder as builder
    from taxondbbuilder.postprep import primer_trim

    fasta = tmp_path / "vsearch.fasta"
    fasta.write_text(">record\nACGTGGGG\n", encoding="utf-8")
    monkeypatch.setattr(
        primer_trim,
        "run_vsearch_endpoint_recheck",
        lambda *args, **kwargs: (1, 0, "stubbed"),
    )
    stats = builder.apply_post_prep_primer_trim(
        fasta,
        ["ACGT"],
        ["TGCA"],
        options={"recheck_tool": "vsearch", "keep_retained_fasta": False},
    )
    assert stats["recheck_attempted"] == 1
    assert stats["recheck_rescued"] == 0
    assert stats["recheck_error"] == "stubbed"
    assert fasta.read_text(encoding="utf-8") == ">record\nGGGG\n"


class _FakeHandle(io.StringIO):
    def __init__(self, payload: Any):
        super().__init__(payload if isinstance(payload, str) else "")
        self.payload = payload

    def close(self) -> None:
        self.closed_by_test = True


def test_fetch_genbank_pages_fallback_retries_dump_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondbbuilder as builder

    search_calls: list[dict[str, Any]] = []
    fetch_calls: list[dict[str, Any]] = []
    fetch_attempts: dict[int, int] = {}

    def esearch(**kwargs: Any):
        search_calls.append(kwargs)
        if kwargs.get("retmax") == 0:
            return _FakeHandle({"Count": "5", "WebEnv": "webenv", "QueryKey": "1"})
        start = kwargs.get("retstart", 0)
        return _FakeHandle({"IdList": [f"id{start}"]})

    def read(handle: _FakeHandle):
        return handle.payload

    def efetch(**kwargs: Any):
        fetch_calls.append(kwargs)
        if "id" in kwargs:
            start = 2
        else:
            start = int(kwargs.get("retstart", 4))
        fetch_attempts[start] = fetch_attempts.get(start, 0) + 1
        attempt = fetch_attempts[start]
        if start == 2 and "id" not in kwargs:
            raise HTTPError(
                "https://example.invalid",
                HTTPStatus.BAD_REQUEST,
                "bad history",
                {},
                None,
            )
        if start == 4 and attempt == 1:
            raise RemoteDisconnected("connection reset")
        return _FakeHandle(f"chunk-{start}")

    monkeypatch.setattr(builder.Entrez, "esearch", esearch)
    monkeypatch.setattr(builder.Entrez, "read", read)
    monkeypatch.setattr(builder.Entrez, "efetch", efetch)
    monkeypatch.setattr(builder.time, "sleep", lambda _seconds: None)

    cfg = {
        "db": "nucleotide",
        "rettype": "gb",
        "retmode": "text",
        "per_query": 2,
        "fetch_retries": 2,
    }
    dump_dir = tmp_path / "gb-cache"
    count, chunks = builder.fetch_genbank(
        "fixture", cfg, 0, dump_dir=dump_dir, taxid="999"
    )
    assert count == 5
    assert list(chunks) == [(0, "chunk-0"), (2, "chunk-2"), (4, "chunk-4")]
    query_cache = next((dump_dir / ".cache" / "taxid999").iterdir())
    assert query_cache.name.startswith("query-")
    assert sorted(path.name for path in query_cache.iterdir()) == [
        "start000000000_count0002.cache",
        "start000000002_count0002.cache",
        "start000000004_count0002.cache",
    ]
    assert fetch_attempts[4] == 2
    assert any("id" in call and call["id"] == "id2" for call in fetch_calls)

    fetch_calls.clear()
    count, chunks = builder.fetch_genbank(
        "fixture", cfg, 0, dump_dir=dump_dir, resume=True, taxid="999"
    )
    assert count == 5
    assert list(chunks) == [(0, "chunk-0"), (2, "chunk-2"), (4, "chunk-4")]
    assert fetch_calls == []


def test_fetch_genbank_resume_cache_is_isolated_by_taxid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondbbuilder as builder

    fetch_calls: list[dict[str, Any]] = []

    def esearch(**kwargs: Any):
        return _FakeHandle(
            {
                "Count": "1",
                "WebEnv": kwargs["term"],
                "QueryKey": "1",
            }
        )

    def read(handle: _FakeHandle):
        return handle.payload

    def efetch(**kwargs: Any):
        fetch_calls.append(kwargs)
        return _FakeHandle(f"chunk-{kwargs['webenv']}")

    monkeypatch.setattr(builder.Entrez, "esearch", esearch)
    monkeypatch.setattr(builder.Entrez, "read", read)
    monkeypatch.setattr(builder.Entrez, "efetch", efetch)
    monkeypatch.setattr(builder.time, "sleep", lambda _seconds: None)

    cfg = {
        "db": "nucleotide",
        "rettype": "gb",
        "retmode": "text",
        "per_query": 100,
    }
    dump_dir = tmp_path / "gb-cache"

    first_count, first_chunks = builder.fetch_genbank(
        "query-first",
        cfg,
        0,
        dump_dir=dump_dir,
        resume=True,
        taxid="111",
    )
    second_count, second_chunks = builder.fetch_genbank(
        "query-second",
        cfg,
        0,
        dump_dir=dump_dir,
        resume=True,
        taxid="222",
    )

    assert first_count == second_count == 1
    assert list(first_chunks) == [(0, "chunk-query-first")]
    assert list(second_chunks) == [(0, "chunk-query-second")]
    assert [call["webenv"] for call in fetch_calls] == [
        "query-first",
        "query-second",
    ]
    assert next((dump_dir / ".cache" / "taxid111").glob("query-*/start*.cache"))
    assert next((dump_dir / ".cache" / "taxid222").glob("query-*/start*.cache"))


def test_fetch_genbank_resume_cache_is_isolated_by_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import taxondbbuilder as builder

    def esearch(**kwargs: Any):
        return _FakeHandle({"Count": "1", "WebEnv": kwargs["term"], "QueryKey": "1"})

    monkeypatch.setattr(builder.Entrez, "esearch", esearch)
    monkeypatch.setattr(builder.Entrez, "read", lambda handle: handle.payload)
    monkeypatch.setattr(
        builder.Entrez,
        "efetch",
        lambda **kwargs: _FakeHandle(f"chunk-{kwargs['webenv']}"),
    )
    monkeypatch.setattr(builder.time, "sleep", lambda _seconds: None)
    cfg = {"db": "nucleotide", "rettype": "gb", "retmode": "text", "per_query": 100}
    dump_dir = tmp_path / "gb-cache"

    for query in ("query-first", "query-second"):
        _, chunks = builder.fetch_genbank(
            query, cfg, 0, dump_dir=dump_dir, resume=True, taxid="999"
        )
        assert list(chunks) == [(0, f"chunk-{query}")]

    assert len(list((dump_dir / ".cache" / "taxid999").glob("query-*"))) == 2

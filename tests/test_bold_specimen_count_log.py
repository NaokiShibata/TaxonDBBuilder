"""`specimens=` in the BOLD log must keep 0 and unknown apart.

BOLD answers a real zero when a taxon has no specimens, and answers nothing
parseable when the summary call cannot produce a count. Collapsing both into 0
makes an API problem look like an empty taxon.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from taxondbbuilder.cli import _run_bold_stage


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


def _ctx(logger: _Logger) -> SimpleNamespace:
    return SimpleNamespace(
        uses_bold=True,
        resolved_taxa=[SimpleNamespace(scientific_name="Tachysurus ichikawai", taxid=1)],
        cfg={},
        bold_queries=[],
        acc_to_seqs={},
        run_logger=logger,
        marker_keys=[],
        marker_map={},
        output_cfg={},
        source=None,
        progress=None,
        lock=None,
        sink=None,
        source_merge_rows=[],
        dump_gb=None,
        resume=False,
    )


def _run_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, specimen_count) -> tuple[_Logger, list]:
    from taxondbbuilder import cli

    logger = _Logger()
    prepared = SimpleNamespace(
        scientific_name="Tachysurus ichikawai",
        raw_query="tax:Tachysurus ichikawai",
        normalized_query="taxon:name:Tachysurus ichikawai",
        specimen_count=specimen_count,
        query_id="tok" if specimen_count != 0 else None,
        runtime_cfg={},
        download_format="tsv",
    )
    calls: list = []
    monkeypatch.setattr(cli, "prepare_bold_query", lambda *_a, **_k: prepared)
    monkeypatch.setattr(
        cli,
        "process_bold_taxon_to_spool",
        lambda resolved, prep, *rest, **_kw: calls.append(prep.specimen_count),
    )
    with tmp_path.joinpath("spool.tsv").open("w", encoding="utf-8") as spool_f:
        _run_bold_stage(_ctx(logger), spool_f, tmp_path)
    return logger, calls


def test_unknown_specimen_count_is_logged_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    logger, calls = _run_stage(monkeypatch, tmp_path, None)
    summary = [line for line in logger.lines if "phase=summary" in line]
    assert summary and "specimens=unknown" in summary[0]
    assert "specimens=0" not in summary[0]
    # The query stage is only announced for a known positive count.
    assert not any("phase=query" in line for line in logger.lines)
    # Fetch behaviour is unchanged: the taxon still goes through the spool stage.
    assert calls == [None]


def test_zero_specimen_count_is_logged_as_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    logger, calls = _run_stage(monkeypatch, tmp_path, 0)
    summary = [line for line in logger.lines if "phase=summary" in line]
    assert summary and "specimens=0" in summary[0]
    assert not any("phase=query" in line for line in logger.lines)
    assert calls == [0]


def test_a_positive_count_is_logged_as_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    logger, calls = _run_stage(monkeypatch, tmp_path, 12)
    summary = [line for line in logger.lines if "phase=summary" in line]
    assert summary and "specimens=12" in summary[0]
    assert any("phase=query" in line for line in logger.lines)
    assert calls == [12]


def test_bold_query_line_reports_unknown_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The closing `# bold query:` line has the same three-state rule."""
    from taxondbbuilder.bold import process_bold_taxon_to_spool
    from taxondbbuilder.bold_api import BoldApiError

    logger = _Logger()
    prepared = SimpleNamespace(
        specimen_count=None,
        query_id="tok",
        normalized_query="taxon:name:Tachysurus ichikawai",
        download_format="tsv",
        runtime_cfg={},
    )

    class _Progress:
        def add_task(self, *_a, **_k):
            return 1

        def update(self, *_a, **_k):
            return None

        def remove_task(self, *_a):
            return None

    class _Sink:
        def bump(self, *_a):
            return None

    monkeypatch.setattr(
        "taxondbbuilder.bold._fetch_bold_documents",
        lambda *_a, **_k: {"path": str(tmp_path / "d.tsv"), "downloaded_bytes": 3},
    )
    monkeypatch.setattr(
        "taxondbbuilder.bold.iter_document_rows_from_path", lambda *_a, **_k: iter(())
    )

    process_bold_taxon_to_spool(
        SimpleNamespace(scientific_name="Tachysurus ichikawai", taxid=1),
        prepared,
        [],
        {},
        {},
        None,
        _Progress(),
        io.StringIO(),
        threading.Lock(),
        _Sink(),
        [],
        set(),
        tmp_path,
        logger,
        cache_dir=tmp_path,
        resume=False,
    )

    closing = [line for line in logger.lines if line.startswith("# bold query:")]
    assert closing and "specimens=unknown" in closing[0]
    assert "specimens=None" not in closing[0]

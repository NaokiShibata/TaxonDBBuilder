"""Guards in tools/bold_api_check.py: the download must stay bounded.

`--max-rows` only caps parsing, and the BOLD download endpoint has no record
limit, so the tool has to refuse a large query before it starts the transfer.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("bold_api_check", ROOT / "tools" / "bold_api_check.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _prepared(specimen_count):
    return SimpleNamespace(
        normalized_query="taxon:name:Tachysurus ichikawai",
        specimen_count=specimen_count,
        query_id="tok" * 20,
    )


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch):
    calls = {"download": 0}

    def fake_download(query_id, cfg, dest, fmt=None, progress_callback=None):
        calls["download"] += 1
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("marker_code\tprocessid\nCOI-5P\tP1\n", encoding="utf-8")
        return {"path": str(dest), "format": "tsv", "downloaded_bytes": 24, "content_length": 24}

    monkeypatch.setattr(check, "download_documents_to_path", fake_download)
    return calls


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str], specimen_count) -> int:
    monkeypatch.setattr(check, "prepare_bold_query", lambda *_a, **_k: _prepared(specimen_count))
    monkeypatch.setattr(sys, "argv", ["bold_api_check.py", "--out", str(tmp_path), *argv])
    return check.main()


def test_a_query_bigger_than_the_cap_is_refused_before_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stubs: dict
):
    code = _run(monkeypatch, tmp_path, ["--max-specimens", "2000"], 500_000)
    assert code == 2
    assert stubs["download"] == 0


def test_an_unknown_specimen_count_is_refused_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stubs: dict
):
    code = _run(monkeypatch, tmp_path, [], None)
    assert code == 2
    assert stubs["download"] == 0


def test_an_unknown_count_downloads_only_when_forced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stubs: dict, capsys: pytest.CaptureFixture[str]
):
    code = _run(monkeypatch, tmp_path, ["--allow-unknown-count"], None)
    assert stubs["download"] == 1
    assert code == 0
    assert "stage 5 ok" in capsys.readouterr().out


def test_a_small_query_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubs: dict):
    code = _run(monkeypatch, tmp_path, [], 12)
    assert code == 0
    assert stubs["download"] == 1


def test_max_rows_still_only_caps_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubs: dict, capsys: pytest.CaptureFixture[str]):
    (tmp_path / "check.tsv").write_text(
        "marker_code\tprocessid\n" + "".join(f"COI-5P\tP{i}\n" for i in range(10)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check, "prepare_bold_query", lambda *_a, **_k: _prepared(12)
    )
    monkeypatch.setattr(
        check,
        "download_documents_to_path",
        lambda query_id, cfg, dest, fmt=None, progress_callback=None: {
            "path": str(dest),
            "format": "tsv",
            "downloaded_bytes": 1,
            "content_length": 1,
        },
    )
    monkeypatch.setattr(sys, "argv", ["bold_api_check.py", "--out", str(tmp_path), "--max-rows", "3"])
    assert check.main() == 0
    assert "rows_read=3 (capped at 3)" in capsys.readouterr().out

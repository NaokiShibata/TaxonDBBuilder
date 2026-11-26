"""Behavior tests for taxdump -> taxonomy.db building (taxondbbuilder.taxonomy_db)."""

from __future__ import annotations

import hashlib
import sqlite3
import tarfile
from pathlib import Path

import pytest

from taxondbbuilder import taxonomy_db


def make_taxdump(tmp_path: Path, rows: list[tuple[int, str, str]], name: str = "taxdump.tar.gz") -> Path:
    names = "".join(
        f"{tax_id}\t|\t{name}\t|\t\t|\t{name_class}\n" for tax_id, name, name_class in rows
    )
    names_path = tmp_path / f"names-{name}.dmp"
    names_path.write_text(names, encoding="utf-8")
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(names_path, arcname="names.dmp")
    return archive


SCIENTIFIC_ROWS = [
    (1, "root", "scientific name"),
    (9606, "Homo sapiens", "scientific name"),
    (9607, "Homo sapiens wasm", "synonym"),  # must not appear in the DB
]


def search(db_path: Path, pattern: str) -> list[tuple[int, str]]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT tax_id, scientific_name FROM taxonomy WHERE scientific_name LIKE ?",
            (pattern,),
        ).fetchall()


def test_fetch_and_build_from_archive_builds_searchable_db(tmp_path: Path) -> None:
    archive = make_taxdump(tmp_path, SCIENTIFIC_ROWS)
    out_db = tmp_path / "out" / "taxonomy.db"

    inserted = taxonomy_db.fetch_and_build(out_db, archive=archive)

    assert inserted == 2
    assert search(out_db, "homo%") == [(9606, "Homo sapiens")]
    # Only artifacts left behind: the published DB, no archive or CSV.
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["taxonomy.db"]


def test_existing_db_never_touched_without_replace(tmp_path: Path) -> None:
    archive = make_taxdump(tmp_path, SCIENTIFIC_ROWS, name="keep.tar.gz")
    out_db = tmp_path / "keep" / "taxonomy.db"
    out_db.parent.mkdir(parents=True)
    out_db.write_bytes(b"previous database")

    with pytest.raises(FileExistsError, match="nothing was replaced"):
        taxonomy_db.fetch_and_build(out_db, archive=archive)

    assert out_db.read_bytes() == b"previous database"


def test_replace_publishes_over_existing_db(tmp_path: Path) -> None:
    archive = make_taxdump(tmp_path, SCIENTIFIC_ROWS, name="repl.tar.gz")
    out_db = tmp_path / "repl" / "taxonomy.db"
    out_db.parent.mkdir(parents=True)
    out_db.write_bytes(b"previous database")

    inserted = taxonomy_db.fetch_and_build(out_db, archive=archive, replace=True)

    assert inserted == 2
    assert search(out_db, "homo%") == [(9606, "Homo sapiens")]
    # The displaced file is kept, never silently destroyed.
    assert (out_db.parent / "taxonomy.db.old").read_bytes() == b"previous database"


def test_replace_displaces_a_file_swapped_during_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_taxdump(tmp_path, SCIENTIFIC_ROWS, name="swap.tar.gz")
    out_db = tmp_path / "swap" / "taxonomy.db"
    out_db.parent.mkdir(parents=True)
    out_db.write_bytes(b"consented database")

    original = taxonomy_db.extract_names_dmp

    def swapping_extract(*args, **kwargs):
        result = original(*args, **kwargs)
        out_db.unlink()
        out_db.write_bytes(b"someone else's database")
        return result

    monkeypatch.setattr(taxonomy_db, "extract_names_dmp", swapping_extract)

    inserted = taxonomy_db.fetch_and_build(out_db, archive=archive, replace=True)

    assert inserted == 2
    # The mid-build foreign file was displaced to .old, not overwritten in place.
    assert (out_db.parent / "taxonomy.db.old").read_bytes() == b"someone else's database"
    assert search(out_db, "homo%") == [(9606, "Homo sapiens")]


@pytest.mark.parametrize("stage", ["download", "missing-names", "corrupt-archive", "empty", "duplicate"])
def test_failures_keep_the_existing_db(tmp_path: Path, stage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    out_db = tmp_path / "taxonomy.db"
    out_db.write_bytes(b"previous database")
    before = hashlib.sha256(out_db.read_bytes()).hexdigest()

    if stage == "download":
        def failing_download(*args, **kwargs):
            raise OSError("network down")

        monkeypatch.setattr(taxonomy_db.urllib.request, "urlopen", failing_download)
        with pytest.raises(OSError):
            taxonomy_db.fetch_and_build(out_db)
    elif stage == "missing-names":
        archive = tmp_path / "taxdump.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            pass
        with pytest.raises(ValueError, match="names.dmp"):
            taxonomy_db.fetch_and_build(out_db, archive=archive)
    elif stage == "corrupt-archive":
        archive = tmp_path / "taxdump.tar.gz"
        archive.write_bytes(b"not a gzip stream")
        with pytest.raises(tarfile.TarError):
            taxonomy_db.fetch_and_build(out_db, archive=archive)
    elif stage == "empty":
        archive = make_taxdump(tmp_path, [(9607, "only a synonym", "synonym")], name="empty.tar.gz")
        with pytest.raises(ValueError, match="inconsistent"):
            taxonomy_db.fetch_and_build(out_db, archive=archive)
    else:
        archive = make_taxdump(
            tmp_path,
            [(9606, "Homo sapiens", "scientific name"), (9606, "Homo sapiens dup", "scientific name")],
            name="dup.tar.gz",
        )
        with pytest.raises(sqlite3.IntegrityError):
            taxonomy_db.fetch_and_build(out_db, archive=archive)

    assert hashlib.sha256(out_db.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".taxonomy-fetch-")) == []


def test_cli_taxonomy_fetch_build_reports_and_exits(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from taxondbbuilder.cli import app

    archive = make_taxdump(tmp_path, SCIENTIFIC_ROWS, name="cli.tar.gz")
    out_db = tmp_path / "cli" / "taxonomy.db"

    result = CliRunner().invoke(app, ["taxonomy-fetch-build", "--out-db", str(out_db), "--archive", str(archive)])

    assert result.exit_code == 0
    assert "inserted 2 rows" in result.stdout
    assert search(out_db, "Salmo%") == []

    bad = tmp_path / "cli" / "missing.tar.gz"
    result = CliRunner().invoke(app, ["taxonomy-fetch-build", "--out-db", str(out_db), "--archive", str(bad)])
    assert result.exit_code == 1
    assert "taxonomy build failed" in result.stderr

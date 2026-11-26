"""Build the taxonomy lookup database (tax_id -> scientific name) used by the GUI.

Shared by ``tools/build_taxonomy_db.py`` and the ``taxonomy-fetch-build`` CLI
command that the Tauri GUI invokes. Standard library only, so the PyInstaller
sidecar can run it without extra runtime dependencies.
"""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterator
from itertools import batched
from pathlib import Path

TAXDUMP_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"


def iter_scientific_names(names_dmp: Path) -> Iterator[tuple[int, str]]:
    """
    Yield (tax_id, scientific_name) from names.dmp where name_class == scientific name.
    """
    with names_dmp.open("r", encoding="utf-8") as fin:
        for raw in fin:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            # taxdump lines end with '\t|'
            line = line.removesuffix("\t|")
            parts = line.split("\t|\t")
            if len(parts) < 4:
                continue

            tax_id_s = parts[0].strip()
            name_txt = parts[1].strip()
            name_class = parts[3].strip()
            if name_class != "scientific name":
                continue
            if not tax_id_s.isdigit() or not name_txt:
                continue
            yield int(tax_id_s), name_txt


def extract_scientific_names_csv(names_dmp: Path, output_csv: Path) -> int:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_csv.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.writer(fout)
        writer.writerow(["tax_id", "scientific_name"])
        for tax_id, sci_name in iter_scientific_names(names_dmp):
            writer.writerow([tax_id, sci_name])
            count += 1
    return count


def iter_csv_rows(input_csv: Path) -> Iterator[tuple[int, str]]:
    with input_csv.open("r", newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {input_csv}")

        # Support both canonical and reversed column order.
        headers = {h.strip(): h for h in reader.fieldnames if h}
        tax_col = headers.get("tax_id")
        sci_col = headers.get("scientific_name")
        if not tax_col or not sci_col:
            raise ValueError("CSV must contain headers: tax_id, scientific_name")

        for row in reader:
            tax_s = str(row.get(tax_col, "")).strip()
            sci_name = str(row.get(sci_col, "")).strip()
            if not tax_s.isdigit() or not sci_name:
                continue
            yield int(tax_s), sci_name


def build_sqlite_from_csv(input_csv: Path, output_db: Path) -> int:
    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists():
        output_db.unlink()

    conn = sqlite3.connect(str(output_db))
    inserted = 0
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE taxonomy (
                tax_id INTEGER PRIMARY KEY,
                scientific_name TEXT NOT NULL
            )
            """
        )

        for batch in batched(iter_csv_rows(input_csv), 20000):
            cur.executemany(
                "INSERT INTO taxonomy (tax_id, scientific_name) VALUES (?, ?)",
                batch,
            )
            inserted += len(batch)

        cur.execute(
            "CREATE INDEX idx_taxonomy_scientific_name_nocase "
            "ON taxonomy(scientific_name COLLATE NOCASE)"
        )
        conn.commit()
    finally:
        conn.close()
    return inserted


def build_from_names_dmp(
    names_dmp: Path, output_csv: Path, output_db: Path
) -> tuple[int, int]:
    extracted = extract_scientific_names_csv(names_dmp, output_csv)
    inserted = build_sqlite_from_csv(output_csv, output_db)
    return extracted, inserted


def download_taxdump(url: str, dest_dir: Path, timeout: float = 180.0) -> Path:
    target = dest_dir / "taxdump.tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": "TaxonDBBuilder"})
    with urllib.request.urlopen(request, timeout=timeout) as resp, target.open("wb") as out:
        shutil.copyfileobj(resp, out)
    return target


def extract_names_dmp(archive_path: Path, dest_dir: Path) -> Path:
    """Read the names.dmp regular file out of a taxdump archive."""
    dest = dest_dir / "names.dmp"
    with tarfile.open(archive_path, "r:gz") as tar:
        try:
            member = tar.getmember("names.dmp")
        except KeyError:
            raise ValueError(f"names.dmp is missing from {archive_path}") from None
        if not member.isfile():
            raise ValueError(f"names.dmp in {archive_path} is not a regular file")
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError(f"names.dmp in {archive_path} could not be read")
        with extracted, dest.open("wb") as out:
            shutil.copyfileobj(extracted, out)
    return dest


def fetch_and_build(
    out_db: Path,
    archive: Path | None = None,
    url: str = TAXDUMP_URL,
    timeout: float = 180.0,
) -> int:
    """Download (or read) taxdump and atomically (re)build out_db.

    The existing database stays untouched unless a complete, verified
    database was produced; staging happens on the output directory so the
    final os.replace stays on one filesystem.
    """
    out_db = out_db.expanduser()
    out_db.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".taxonomy-fetch-", dir=out_db.parent) as tmp:
        tmp_path = Path(tmp)
        if archive is not None:
            archive_path = Path(archive).expanduser()
        else:
            archive_path = download_taxdump(url, tmp_path, timeout)
        names_dmp = extract_names_dmp(archive_path, tmp_path)
        extracted, inserted = build_from_names_dmp(
            names_dmp, tmp_path / "taxid_scientific_name.csv", tmp_path / "taxonomy.db"
        )
        if extracted < 1 or inserted != extracted:
            raise ValueError(
                f"taxonomy build is inconsistent: extracted={extracted} inserted={inserted}"
            )
        with sqlite3.connect(tmp_path / "taxonomy.db") as conn:
            (check,) = conn.execute("PRAGMA quick_check").fetchone()
        if check != "ok":
            raise ValueError(f"generated taxonomy.db failed quick_check: {check}")
        os.replace(tmp_path / "taxonomy.db", out_db)
    return inserted

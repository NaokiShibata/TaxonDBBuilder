"""Unambiguous pairing of FASTA records with the source rows that produced them."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from Bio import SeqIO

__all__ = ["HeaderCollisionError", "pair_fasta_with_rows"]


class HeaderCollisionError(ValueError):
    """A FASTA header cannot be paired unambiguously with one source row."""


def pair_fasta_with_rows(
    fasta_path: Path, emitted_records: list[dict[str, str]]
) -> list[tuple[Any, dict[str, str] | None]]:
    """Return (SeqRecord, source_row_or_None) pairs in FASTA order."""
    rows_by_header: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in emitted_records:
        rows_by_header[row.get("header", "")].append(row)
    for header, rows in rows_by_header.items():
        if len(rows) > 1:
            raise HeaderCollisionError(
                f"FASTA header {header!r} maps to {len(rows)} source rows; "
                "cannot pair records by header"
            )

    paired: list[tuple[Any, dict[str, str] | None]] = []
    seen: set[str] = set()
    for record in SeqIO.parse(fasta_path, "fasta"):
        header = str(record.description).strip()
        if header in seen:
            raise HeaderCollisionError(
                f"FASTA header {header!r} appears more than once in the FASTA; "
                "cannot pair records by header"
            )
        seen.add(header)
        rows = rows_by_header.get(header)
        paired.append((record, rows[0] if rows else None))
    return paired

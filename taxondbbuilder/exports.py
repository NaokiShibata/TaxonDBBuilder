"""Optional exports for downstream taxonomy assignment tools."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from Bio import SeqIO

from .headers import sanitize_header
from .models import ExportFormat


def _clean_taxon_name(value: str) -> str:
    return " ".join(value.replace("\t", " ").split())


def _species_binomial(value: str) -> str | None:
    """Return a strict Genus species name accepted by DADA2 assignSpecies."""
    parts = _clean_taxon_name(value).split()
    if len(parts) < 2:
        return None
    genus, species = parts[:2]
    if not re.fullmatch(r"[A-Z][A-Za-z.-]*", genus):
        return None
    if not re.fullmatch(r"[a-z][A-Za-z.-]*", species):
        return None
    if species in {"sp", "sp.", "cf", "cf.", "aff", "aff."}:
        return None
    return f"{genus} {species}"


def _unique_feature_id(raw: str, seen: dict[str, int], fallback_index: int) -> str:
    base = sanitize_header(raw) or f"feature_{fallback_index}"
    seen[base] += 1
    return base if seen[base] == 1 else f"{base}__{seen[base]}"


def _collect_final_records(
    fasta_path: Path, emitted_records: list[dict[str, str]]
) -> tuple[list[dict[str, str]], int]:
    records_by_header: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in emitted_records:
        records_by_header[row.get("header", "")].append(row)

    offsets: dict[str, int] = defaultdict(int)
    seen_ids: dict[str, int] = defaultdict(int)
    mapped: list[dict[str, str]] = []
    unmapped = 0
    with fasta_path.open("r", encoding="utf-8") as fasta_f:
        for index, record in enumerate(SeqIO.parse(fasta_f, "fasta"), start=1):
            header = str(record.description).strip()
            rows = records_by_header.get(header, [])
            offset = offsets[header]
            if offset >= len(rows):
                unmapped += 1
                continue
            source = rows[offset]
            offsets[header] += 1
            mapped.append(
                {
                    "feature_id": _unique_feature_id(
                        source.get("acc_id", "") or str(record.id), seen_ids, index
                    ),
                    "organism_name": _clean_taxon_name(
                        source.get("organism_name", "")
                    ),
                    "sequence": str(record.seq).upper(),
                }
            )
    return mapped, unmapped


def _write_qiime2(
    fasta_path: Path, records: list[dict[str, str]], unmapped: int
) -> dict[str, Any]:
    sequences_path = fasta_path.with_suffix(
        fasta_path.suffix + ".qiime2.sequences.fasta"
    )
    taxonomy_path = fasta_path.with_suffix(fasta_path.suffix + ".qiime2.taxonomy.tsv")
    with (
        sequences_path.open("w", encoding="utf-8") as sequences_f,
        taxonomy_path.open("w", encoding="utf-8", newline="") as taxonomy_f,
    ):
        taxonomy_f.write("Feature ID\tTaxon\n")
        for record in records:
            sequences_f.write(f">{record['feature_id']}\n{record['sequence']}\n")
            taxonomy_f.write(
                f"{record['feature_id']}\t{record['organism_name'] or 'Unassigned'}\n"
            )
    return {
        "format": ExportFormat.QIIME2.value,
        "paths": [sequences_path, taxonomy_path],
        "exported_records": len(records),
        "skipped_records": unmapped,
    }


def _write_dada2_species(
    fasta_path: Path, records: list[dict[str, str]], unmapped: int
) -> dict[str, Any]:
    output_path = fasta_path.with_suffix(
        fasta_path.suffix + ".dada2.species.fasta"
    )
    exported = 0
    skipped = unmapped
    with output_path.open("w", encoding="utf-8") as output_f:
        for record in records:
            binomial = _species_binomial(record["organism_name"])
            if binomial is None:
                skipped += 1
                continue
            output_f.write(
                f">{record['feature_id']} {binomial}\n{record['sequence']}\n"
            )
            exported += 1
    return {
        "format": ExportFormat.DADA2_SPECIES.value,
        "paths": [output_path],
        "exported_records": exported,
        "skipped_records": skipped,
    }


def write_interoperability_exports(
    fasta_path: Path,
    emitted_records: list[dict[str, str]],
    formats: Iterable[ExportFormat | str],
) -> list[dict[str, Any]]:
    """Write selected downstream formats without changing the primary FASTA."""
    selected = [ExportFormat(value) for value in formats]
    if not selected:
        return []
    records, unmapped = _collect_final_records(fasta_path, emitted_records)
    results: list[dict[str, Any]] = []
    for export_format in selected:
        if export_format == ExportFormat.QIIME2:
            results.append(_write_qiime2(fasta_path, records, unmapped))
        elif export_format == ExportFormat.DADA2_SPECIES:
            results.append(_write_dada2_species(fasta_path, records, unmapped))
    return results

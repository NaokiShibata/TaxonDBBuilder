"""Optional exports for downstream taxonomy assignment tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .headers import sanitize_header
from .models import ExportFormat
from .record_index import pair_fasta_with_rows


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


def _unique_feature_id(raw: str, seen: set[str], fallback_index: int) -> str:
    base = sanitize_header(raw) or f"feature_{fallback_index}"
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = f"{base}__{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _collect_final_records(
    fasta_path: Path, emitted_records: list[dict[str, str]]
) -> tuple[list[dict[str, str]], int]:
    paired = pair_fasta_with_rows(fasta_path, emitted_records)
    seen_ids: set[str] = set()
    mapped: list[dict[str, str]] = []
    for index, (record, source) in enumerate(paired, start=1):
        if source is None:
            continue
        mapped.append(
            {
                "feature_id": _unique_feature_id(
                    source.get("acc_id", "") or str(record.id), seen_ids, index
                ),
                "organism_name": _clean_taxon_name(source.get("organism_name", "")),
                "taxonomy_lineage": source.get("taxonomy_lineage", ""),
                "sequence": str(record.seq).upper(),
            }
        )
    unmapped = sum(1 for _, row in paired if row is None)
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
            lineage = [
                value.strip()
                for value in record["taxonomy_lineage"].split(";")
                if value.strip()
            ]
            if record["organism_name"] and (
                not lineage or lineage[-1] != record["organism_name"]
            ):
                lineage.append(record["organism_name"])
            taxonomy = "; ".join(lineage)
            taxonomy_f.write(f"{record['feature_id']}\t{taxonomy or 'Unassigned'}\n")
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
    selected = list(dict.fromkeys(ExportFormat(value) for value in formats))
    if not selected:
        return []
    if len(selected) > 1:
        raise ValueError("Only one output export format can be selected.")
    records, unmapped = _collect_final_records(fasta_path, emitted_records)
    results: list[dict[str, Any]] = []
    for export_format in selected:
        if export_format == ExportFormat.QIIME2:
            results.append(_write_qiime2(fasta_path, records, unmapped))
        elif export_format == ExportFormat.DADA2_SPECIES:
            results.append(_write_dada2_species(fasta_path, records, unmapped))
    return results

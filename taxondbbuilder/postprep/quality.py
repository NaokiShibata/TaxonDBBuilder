"""Sequence quality and duplicate-policy filtering."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from Bio import SeqIO

IUPAC_DNA_ALPHABET = set("ACGTRYSWKMBDHVN")


def apply_post_prep_quality_filter(
    fasta_path: Path,
    emitted_records: list[dict[str, str]],
    *,
    max_ambiguous_fraction: float | None,
    reject_invalid_iupac: bool,
    duplicate_policy: str,
) -> dict[str, Any]:
    records = list(SeqIO.parse(fasta_path, "fasta"))
    organisms: dict[str, list[str]] = defaultdict(list)
    for row in emitted_records:
        organisms[row.get("header", "")].append(row.get("organism_name", ""))
    offsets: dict[str, int] = defaultdict(int)
    rows = []
    for record in records:
        header = str(record.description).strip()
        index = offsets[header]
        offsets[header] += 1
        names = organisms.get(header, [])
        sequence = str(record.seq).upper()
        rows.append(
            {
                "record": record,
                "header": header,
                "organism_name": names[index] if index < len(names) else "",
                "sequence": sequence,
                "ambiguous_fraction": (
                    sum(base not in "ACGT" for base in sequence) / len(sequence)
                    if sequence
                    else 1.0
                ),
                "reason": "",
            }
        )

    for row in rows:
        invalid = sorted(set(row["sequence"]) - IUPAC_DNA_ALPHABET)
        if reject_invalid_iupac and invalid:
            row["reason"] = f"invalid_iupac:{''.join(invalid)}"
        elif (
            max_ambiguous_fraction is not None
            and row["ambiguous_fraction"] > max_ambiguous_fraction
        ):
            row["reason"] = "too_many_ambiguous_bases"

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row["reason"]:
            groups[row["sequence"]].append(row)
    for group in groups.values():
        if duplicate_policy == "representative":
            for row in group[1:]:
                row["reason"] = "duplicate_sequence"
        elif duplicate_policy == "exclude_conflicts":
            names = {row["organism_name"] for row in group if row["organism_name"]}
            if len(names) > 1:
                for row in group:
                    row["reason"] = "taxonomy_conflict"
            else:
                for row in group[1:]:
                    row["reason"] = "duplicate_sequence"

    report_path = fasta_path.with_suffix(fasta_path.suffix + ".quality_rejected.csv")
    with report_path.open("w", newline="", encoding="utf-8") as report_f:
        writer = csv.DictWriter(
            report_f,
            fieldnames=[
                "header",
                "organism_name",
                "reason",
                "sequence_length",
                "ambiguous_fraction",
            ],
        )
        writer.writeheader()
        for row in rows:
            if row["reason"]:
                writer.writerow(
                    {
                        "header": row["header"],
                        "organism_name": row["organism_name"],
                        "reason": row["reason"],
                        "sequence_length": len(row["sequence"]),
                        "ambiguous_fraction": f"{row['ambiguous_fraction']:.6f}",
                    }
                )

    tmp_path = fasta_path.with_suffix(fasta_path.suffix + ".quality.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as output_f:
            SeqIO.write(
                (row["record"] for row in rows if not row["reason"]),
                output_f,
                "fasta",
            )
        tmp_path.replace(fasta_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    rejected = sum(bool(row["reason"]) for row in rows)
    return {
        "before": len(rows),
        "after": len(rows) - rejected,
        "rejected": rejected,
        "report_path": report_path,
    }

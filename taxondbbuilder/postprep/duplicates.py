"""Duplicate and accession/organism sidecar reports."""

import csv
import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from Bio import SeqIO

from ..headers import compile_header_extractors, extract_header_fields_from_header

def write_duplicate_acc_reports_csv(
    fasta_path: Path,
    header_formats: Iterable[str],
) -> Tuple[Optional[Path], Optional[Path], Dict[str, int], Optional[str]]:
    extractors, has_acc_id_template, has_organism_template = compile_header_extractors(header_formats)
    if not has_acc_id_template:
        return None, None, {}, "selected header format does not include {acc_id}"
    if not has_organism_template:
        return None, None, {}, "selected header format does not include {organism_raw} or {organism}"
    if not extractors:
        return None, None, {}, "could not compile header extractor"
    if not any(extractor.captures_organism for extractor in extractors):
        return None, None, {}, "selected header format does not include {organism_raw} or {organism} alongside {acc_id}"

    seq_groups: Dict[str, List[Dict[str, str]]] = {}
    total_records = 0
    parsed_records = 0
    unparsed_records = 0
    with fasta_path.open("r", encoding="utf-8") as fasta_f:
        for record in SeqIO.parse(fasta_f, "fasta"):
            total_records += 1
            header = str(record.description).strip()
            acc_id, organism_name = extract_header_fields_from_header(header, extractors)
            if not acc_id or not organism_name:
                unparsed_records += 1
                continue
            parsed_records += 1
            accession = re.sub(r"_dup\d+$", "", acc_id)
            seq = str(record.seq).upper()
            seq_groups.setdefault(seq, []).append(
                {
                    "acc_id": acc_id,
                    "accession": accession,
                    "organism_name": organism_name,
                    "header": header,
                }
            )

    records_path = fasta_path.with_suffix(fasta_path.suffix + ".duplicate_acc.records.csv")
    groups_path = fasta_path.with_suffix(fasta_path.suffix + ".duplicate_acc.groups.csv")
    duplicate_groups = 0
    duplicate_records = 0
    cross_organism_groups = 0
    with records_path.open("w", newline="", encoding="utf-8") as records_f, groups_path.open(
        "w", newline="", encoding="utf-8"
    ) as groups_f:
        records_writer = csv.DictWriter(
            records_f,
            fieldnames=[
                "group_id",
                "sequence_hash",
                "sequence_length",
                "records_in_group",
                "unique_accessions",
                "unique_organisms",
                "cross_organism_duplicate",
                "acc_id",
                "accession",
                "organism_name",
                "header",
            ],
        )
        records_writer.writeheader()
        groups_writer = csv.DictWriter(
            groups_f,
            fieldnames=[
                "group_id",
                "sequence_hash",
                "sequence_length",
                "records_in_group",
                "unique_accessions",
                "unique_organisms",
                "cross_organism_duplicate",
                "accessions",
                "organism_names",
            ],
        )
        groups_writer.writeheader()

        group_id = 0
        for seq, entries in sorted(seq_groups.items(), key=lambda item: len(item[1]), reverse=True):
            if len(entries) < 2:
                continue
            unique_accessions = sorted({item["accession"] for item in entries})
            if len(unique_accessions) < 2:
                continue
            unique_organisms = sorted({item["organism_name"] for item in entries})
            is_cross_organism = len(unique_organisms) > 1
            group_id += 1
            duplicate_groups += 1
            duplicate_records += len(entries)
            if is_cross_organism:
                cross_organism_groups += 1
            seq_hash = hashlib.sha1(seq.encode("utf-8")).hexdigest()
            groups_writer.writerow(
                {
                    "group_id": group_id,
                    "sequence_hash": seq_hash,
                    "sequence_length": len(seq),
                    "records_in_group": len(entries),
                    "unique_accessions": len(unique_accessions),
                    "unique_organisms": len(unique_organisms),
                    "cross_organism_duplicate": str(is_cross_organism).lower(),
                    "accessions": ";".join(unique_accessions),
                    "organism_names": ";".join(unique_organisms),
                }
            )
            for item in entries:
                records_writer.writerow(
                    {
                        "group_id": group_id,
                        "sequence_hash": seq_hash,
                        "sequence_length": len(seq),
                        "records_in_group": len(entries),
                        "unique_accessions": len(unique_accessions),
                        "unique_organisms": len(unique_organisms),
                        "cross_organism_duplicate": str(is_cross_organism).lower(),
                        "acc_id": item["acc_id"],
                        "accession": item["accession"],
                        "organism_name": item["organism_name"],
                        "header": item["header"],
                    }
                )

    stats = {
        "total_records": total_records,
        "parsed_records": parsed_records,
        "unparsed_records": unparsed_records,
        "duplicate_groups": duplicate_groups,
        "duplicate_records": duplicate_records,
        "cross_organism_groups": cross_organism_groups,
    }
    return records_path, groups_path, stats, None


def write_acc_organism_mapping_csv(
    fasta_path: Path,
    emitted_records: List[Dict[str, str]],
) -> Tuple[Path, Dict[str, int]]:
    mapping_path = fasta_path.with_suffix(fasta_path.suffix + ".acc_organism.csv")
    records_by_header: Dict[str, List[Dict[str, str]]] = {}
    for row in emitted_records:
        header = row.get("header", "")
        records_by_header.setdefault(header, []).append(row)

    offsets: Dict[str, int] = {}
    total_records = 0
    mapped_records = 0
    unmapped_records = 0
    unique_accessions = set()
    unique_organisms = set()
    mapped_rows: List[Dict[str, str]] = []
    with fasta_path.open("r", encoding="utf-8") as fasta_f:
        for record in SeqIO.parse(fasta_f, "fasta"):
            total_records += 1
            header = str(record.description).strip()
            rows = records_by_header.get(header)
            idx = offsets.get(header, 0)
            if not rows or idx >= len(rows):
                unmapped_records += 1
                continue
            row = rows[idx]
            offsets[header] = idx + 1
            mapped_records += 1
            unique_accessions.add(row["accession"])
            unique_organisms.add(row["organism_name"])
            mapped_rows.append(
                {
                    "acc_id": row["acc_id"],
                    "accession": row["accession"],
                    "organism_name": row["organism_name"],
                    "header": header,
                    "source": row.get("source", ""),
                    "source_record_id": row.get("source_record_id", ""),
                    "processid": row.get("processid", ""),
                    "sampleid": row.get("sampleid", ""),
                    "marker_key": row.get("marker_key", ""),
                    "linked_to_ncbi": row.get("linked_to_ncbi", ""),
                    "emitted_to_fasta": row.get("emitted_to_fasta", ""),
                    "skip_reason": row.get("skip_reason", ""),
                }
            )

    mapped_rows.sort(key=lambda row: (row["acc_id"], row["accession"], row["organism_name"], row["header"]))

    with mapping_path.open("w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(
            out_f,
            fieldnames=[
                "acc_id",
                "accession",
                "organism_name",
                "header",
                "source",
                "source_record_id",
                "processid",
                "sampleid",
                "marker_key",
                "linked_to_ncbi",
                "emitted_to_fasta",
                "skip_reason",
            ],
        )
        writer.writeheader()
        writer.writerows(mapped_rows)

    unused_records = 0
    for header, rows in records_by_header.items():
        unused_records += max(0, len(rows) - offsets.get(header, 0))

    stats = {
        "total_records": total_records,
        "mapped_records": mapped_records,
        "unmapped_records": unmapped_records,
        "unused_records": unused_records,
        "unique_accessions": len(unique_accessions),
        "unique_organisms": len(unique_organisms),
    }
    return mapping_path, stats

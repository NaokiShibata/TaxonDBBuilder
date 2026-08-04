"""Core data models and spool serialization helpers."""

import csv
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from string import Formatter
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from Bio.Data.IUPACData import ambiguous_dna_values

DEFAULT_FEATURE_TYPES = ["rRNA", "gene", "CDS"]
DEFAULT_FEATURE_FIELDS = ["gene", "product", "note", "standard_name"]
DEFAULT_HEADER_FORMAT = "{acc_id}|{organism}|{marker}|{label}|{type}|{loc}|{strand}"
DEFAULT_BOLD_HEADER_FORMAT = "bold|{acc_id}|{organism}"
FORMATTER = Formatter()
IUPAC_DNA_VALUES = {k.upper(): v.upper() for k, v in ambiguous_dna_values.items()}
IUPAC_DNA_VALUES["U"] = "T"


class PostPrepStep(str, Enum):
    PRIMER_TRIM = "primer_trim"
    LENGTH_FILTER = "length_filter"
    DUPLICATE_REPORT = "duplicate_report"


class BuildSource(str, Enum):
    NCBI = "ncbi"
    BOLD = "bold"
    BOTH = "both"


@dataclass(frozen=True)
class ResolvedTaxon:
    input_value: str
    taxid: str
    scientific_name: str
    warning: Optional[str] = None


@dataclass
class CanonicalRecord:
    source: str
    source_record_id: str
    accession: Optional[str]
    processid: Optional[str]
    sampleid: Optional[str]
    taxon_name: Optional[str]
    marker_key: str
    marker_label: Optional[str]
    sequence: str
    header_values: Dict[str, str]
    metadata: Dict[str, str]
    linked_to_ncbi: bool = False
    emitted_to_fasta: bool = True
    skip_reason: Optional[str] = None


POST_PREP_STEP_ORDER = [
    PostPrepStep.PRIMER_TRIM.value,
    PostPrepStep.LENGTH_FILTER.value,
    PostPrepStep.DUPLICATE_REPORT.value,
]

PRIMER_TRIM_MODE_BOTH_REQUIRED = "both_required"
PRIMER_TRIM_MODE_ONE_OR_BOTH = "one_or_both"
PRIMER_TRIM_MODE_ONE_END_MARK_ONLY = "one_end_mark_only"
PRIMER_TRIM_MODES = {
    PRIMER_TRIM_MODE_BOTH_REQUIRED,
    PRIMER_TRIM_MODE_ONE_OR_BOTH,
    PRIMER_TRIM_MODE_ONE_END_MARK_ONLY,
}

def build_source_merge_row(record: CanonicalRecord, header: str = "") -> Dict[str, str]:
    return {
        "source": record.source,
        "source_record_id": record.source_record_id,
        "acc_id": record.header_values.get("acc_id", ""),
        "accession": record.accession or "",
        "processid": record.processid or "",
        "sampleid": record.sampleid or "",
        "organism_name": record.taxon_name or "",
        "marker_key": record.marker_key,
        "marker_label": record.marker_label or "",
        "linked_to_ncbi": str(record.linked_to_ncbi).lower(),
        "emitted_to_fasta": str(record.emitted_to_fasta).lower(),
        "skip_reason": record.skip_reason or "",
        "header": header,
    }


def canonical_record_sort_key(record: CanonicalRecord) -> Tuple[str, str, str]:
    return (
        record.taxon_name or "",
        record.marker_key,
        record.source_record_id,
    )


def canonical_record_to_dict(record: CanonicalRecord) -> Dict[str, Any]:
    return {
        "source": record.source,
        "source_record_id": record.source_record_id,
        "accession": record.accession,
        "processid": record.processid,
        "sampleid": record.sampleid,
        "taxon_name": record.taxon_name,
        "marker_key": record.marker_key,
        "marker_label": record.marker_label,
        "sequence": record.sequence,
        "header_values": record.header_values,
        "metadata": record.metadata,
        "linked_to_ncbi": record.linked_to_ncbi,
        "emitted_to_fasta": record.emitted_to_fasta,
        "skip_reason": record.skip_reason,
    }


def canonical_record_from_dict(data: Dict[str, Any]) -> CanonicalRecord:
    return CanonicalRecord(
        source=str(data.get("source", "")),
        source_record_id=str(data.get("source_record_id", "")),
        accession=data.get("accession"),
        processid=data.get("processid"),
        sampleid=data.get("sampleid"),
        taxon_name=data.get("taxon_name"),
        marker_key=str(data.get("marker_key", "")),
        marker_label=data.get("marker_label"),
        sequence=str(data.get("sequence", "")),
        header_values=dict(data.get("header_values") or {}),
        metadata=dict(data.get("metadata") or {}),
        linked_to_ncbi=bool(data.get("linked_to_ncbi", False)),
        emitted_to_fasta=bool(data.get("emitted_to_fasta", True)),
        skip_reason=data.get("skip_reason"),
    )


def append_records_to_spool(records: List[CanonicalRecord], spool_f, lock: Lock) -> None:
    if not records:
        return
    with lock:
        for record in records:
            spool_f.write(json.dumps(canonical_record_to_dict(record), ensure_ascii=False) + "\n")


def load_records_from_spool(spool_path: Path) -> List[CanonicalRecord]:
    records: List[CanonicalRecord] = []
    if not spool_path.exists():
        return records
    with spool_path.open("r", encoding="utf-8") as in_f:
        for line in in_f:
            raw = line.strip()
            if not raw:
                continue
            records.append(canonical_record_from_dict(json.loads(raw)))
    return records


def write_source_merge_csv(fasta_path: Path, rows: List[Dict[str, str]]) -> Path:
    merge_path = fasta_path.with_suffix(fasta_path.suffix + ".source_merge.csv")
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            row.get("source", ""),
            row.get("organism_name", ""),
            row.get("marker_key", ""),
            row.get("source_record_id", ""),
        ),
    )
    with merge_path.open("w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(
            out_f,
            fieldnames=[
                "source",
                "source_record_id",
                "acc_id",
                "accession",
                "processid",
                "sampleid",
                "organism_name",
                "marker_key",
                "marker_label",
                "linked_to_ncbi",
                "emitted_to_fasta",
                "skip_reason",
                "header",
            ],
        )
        writer.writeheader()
        writer.writerows(sorted_rows)
    return merge_path


"""Core data models and spool serialization helpers."""

import csv
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from Bio.Data.IUPACData import ambiguous_dna_values

DEFAULT_FEATURE_TYPES = ["rRNA", "gene", "CDS"]
DEFAULT_FEATURE_FIELDS = ["gene", "product", "note", "standard_name"]
DEFAULT_HEADER_FORMAT = "{acc_id}|{organism}|{marker}|{label}|{type}|{loc}|{strand}"
DEFAULT_BOLD_HEADER_FORMAT = "bold|{acc_id}|{organism}"
IUPAC_DNA_VALUES = {k.upper(): v.upper() for k, v in ambiguous_dna_values.items()}
IUPAC_DNA_VALUES["U"] = "T"


class PostPrepStep(str, Enum):
    PRIMER_TRIM = "primer_trim"
    LENGTH_FILTER = "length_filter"
    QUALITY_FILTER = "quality_filter"
    DUPLICATE_REPORT = "duplicate_report"
    MSA_TREE = "msa_tree"


class BuildSource(str, Enum):
    NCBI = "ncbi"
    BOLD = "bold"
    BOTH = "both"


class ExportFormat(str, Enum):
    QIIME2 = "qiime2"
    DADA2_SPECIES = "dada2_species"


@dataclass(frozen=True)
class ResolvedTaxon:
    input_value: str
    taxid: str
    scientific_name: str
    warning: str | None = None


@dataclass
class CanonicalRecord:
    source: str
    source_record_id: str
    accession: str | None
    processid: str | None
    sampleid: str | None
    taxon_name: str | None
    marker_key: str
    marker_label: str | None
    sequence: str
    header_values: dict[str, str]
    metadata: dict[str, str]
    linked_to_ncbi: bool = False
    emitted_to_fasta: bool = True
    skip_reason: str | None = None
    taxid: str | None = None
    organism_taxid: str | None = None
    taxonomy_lineage: list[str] = field(default_factory=list)


POST_PREP_STEP_ORDER = [
    PostPrepStep.PRIMER_TRIM.value,
    PostPrepStep.LENGTH_FILTER.value,
    PostPrepStep.QUALITY_FILTER.value,
    PostPrepStep.DUPLICATE_REPORT.value,
    PostPrepStep.MSA_TREE.value,
]

PRIMER_TRIM_MODE_BOTH_REQUIRED = "both_required"
PRIMER_TRIM_MODE_ONE_OR_BOTH = "one_or_both"
PRIMER_TRIM_MODE_ONE_END_MARK_ONLY = "one_end_mark_only"
PRIMER_TRIM_MODES = {
    PRIMER_TRIM_MODE_BOTH_REQUIRED,
    PRIMER_TRIM_MODE_ONE_OR_BOTH,
    PRIMER_TRIM_MODE_ONE_END_MARK_ONLY,
}


def build_source_merge_row(record: CanonicalRecord, header: str = "") -> dict[str, str]:
    return {
        "source": record.source,
        "source_record_id": record.source_record_id,
        "taxid": record.taxid or "",
        "organism_taxid": record.organism_taxid or "",
        "taxonomy_lineage": "; ".join(record.taxonomy_lineage),
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


def canonical_record_sort_key(record: CanonicalRecord) -> tuple[str, str, str]:
    return (
        record.taxon_name or "",
        record.marker_key,
        record.source_record_id,
    )


def canonical_record_to_dict(record: CanonicalRecord) -> dict[str, Any]:
    data = asdict(record)
    if record.taxid is None:
        data.pop("taxid", None)
    return data


def canonical_record_from_dict(data: dict[str, Any]) -> CanonicalRecord:
    return CanonicalRecord(**data)


def append_records_to_spool(
    records: list[CanonicalRecord], spool_f, lock: Lock
) -> None:
    if not records:
        return
    with lock:
        for record in records:
            spool_f.write(
                json.dumps(canonical_record_to_dict(record), ensure_ascii=False) + "\n"
            )


def load_records_from_spool(spool_path: Path) -> list[CanonicalRecord]:
    records: list[CanonicalRecord] = []
    if not spool_path.exists():
        return records
    with spool_path.open("r", encoding="utf-8") as in_f:
        for line in in_f:
            raw = line.strip()
            if not raw:
                continue
            records.append(canonical_record_from_dict(json.loads(raw)))
    return records


def write_source_merge_csv(fasta_path: Path, rows: list[dict[str, str]]) -> Path:
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
                "taxid",
                "organism_taxid",
                "taxonomy_lineage",
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


@dataclass
class ExtractionSink:
    """Shared, lock-guarded accumulation of extraction results.

    NCBI extraction runs on worker threads while BOLD spooling runs on the main
    thread, and both update the same counters and accession index. Every
    mutation goes through this object so neither side has to carry the four
    mutable containers and the lock down its own call chain.
    """

    counters: dict[str, int] = field(
        default_factory=lambda: {
            "total_records": 0,
            "matched_records": 0,
            "matched_features": 0,
            "kept_records": 0,
            "skipped_same": 0,
            "duplicated_diff": 0,
        }
    )
    acc_to_seqs: dict[str, set[str]] = field(default_factory=dict)
    dup_accessions: dict[str, int] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def bump(self, *keys: str) -> None:
        with self.lock:
            for key in keys:
                self.counters[key] += 1

    def bump_optional(self, key: str) -> None:
        """Count a key that may not exist yet, such as a region fallback status."""
        with self.lock:
            self.counters[key] = self.counters.get(key, 0) + 1

    def add_feature(
        self, acc: str, sequence: str, build: Callable[[int | None], Any]
    ) -> Any:
        """Register one matched feature and return its record, or None if deduplicated.

        The record is built inside the lock because its duplicate index comes
        from the accession index being updated.
        """
        with self.lock:
            self.counters["matched_features"] += 1
            seqs = self.acc_to_seqs.setdefault(acc, set())
            if sequence in seqs:
                self.counters["skipped_same"] += 1
                return None
            dup_index = None
            if seqs:
                dup_index = len(seqs) + 1
                self.counters["duplicated_diff"] += 1
                self.dup_accessions[acc] = self.dup_accessions.get(acc, 1) + 1
            seqs.add(sequence)
            return build(dup_index)

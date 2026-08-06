"""FASTA and source sidecar output helpers."""

from threading import Lock

from .headers import build_header
from .models import DEFAULT_HEADER_FORMAT, CanonicalRecord, build_source_merge_row


def emit_records_to_fasta(
    records: list[CanonicalRecord],
    out_f,
    counters: dict[str, int],
    emitted_records: list[dict[str, str]],
    lock: Lock,
    source_merge_rows: list[dict[str, str]] | None = None,
) -> None:
    for record in records:
        if not record.emitted_to_fasta:
            continue

        header_template = record.metadata.get("header_format") or DEFAULT_HEADER_FORMAT
        header = build_header(header_template, record.header_values).strip()
        if not header:
            header = record.header_values.get("acc_id") or record.source_record_id

        with lock:
            out_f.write(f">{header}\n")
            out_f.write(f"{record.sequence}\n")
            counters["kept_records"] += 1
            emitted_row = build_source_merge_row(record, header=header)
            emitted_records.append(dict(emitted_row))
            if source_merge_rows is not None:
                source_merge_rows.append(dict(emitted_row))

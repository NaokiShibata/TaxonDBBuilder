"""FASTA and source sidecar output helpers."""

from threading import Lock

from .headers import build_header
from .models import DEFAULT_HEADER_FORMAT, CanonicalRecord, build_source_merge_row


def _record_header(record: CanonicalRecord) -> str:
    header_template = record.metadata.get("header_format") or DEFAULT_HEADER_FORMAT
    header = build_header(header_template, record.header_values).strip()
    if not header:
        header = record.header_values.get("acc_id") or record.source_record_id
    region_id = record.metadata.get("region_id")
    return f"{header}|region={region_id}" if region_id else header


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

        header = _record_header(record)

        with lock:
            out_f.write(f">{header}\n")
            out_f.write(f"{record.sequence}\n")
            counters["kept_records"] += 1
            emitted_row = build_source_merge_row(record, header=header)
            emitted_records.append(dict(emitted_row))
            if source_merge_rows is not None:
                source_merge_rows.append(dict(emitted_row))


def write_region_fallback_tsv(fasta_path, records):
    """Write extraction provenance before post-prep changes the FASTA."""
    import csv

    columns = [
        "acc_id", "accession", "marker_key", "header", "region_id", "status",
        "extraction_method", "fallback_reason", "boundary_evidence", "original_location",
        "inferred_location", "strand", "inferred_length", "left_flank_id",
        "left_flank_location", "right_flank_id", "right_flank_location", "profile",
        "transl_table", "overlap_bases", "coordinate_system", "stage",
    ]
    path = fasta_path.with_suffix(fasta_path.suffix + ".region_fallback.tsv")
    count = 0
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for record in records:
            if not record.emitted_to_fasta or "extraction_method" not in record.metadata:
                continue
            header = _record_header(record)
            row = {column: record.metadata.get(column, "") for column in columns}
            row.update(acc_id=record.header_values.get("acc_id", ""), accession=record.accession or "",
                       marker_key=record.marker_key,
                       header=header or record.header_values.get("acc_id") or record.source_record_id)
            writer.writerow(row)
            count += 1
    return path, count

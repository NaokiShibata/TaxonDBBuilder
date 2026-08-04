"""BOLD spool orchestration helpers."""

import hashlib
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

import typer
from rich.progress import Progress

from .bold_api import (
    BoldApiError,
    download_documents_to_path,
    iter_document_rows_from_path,
    normalize_bold_row,
    parse_accession_tokens,
    prepare_bold_query,
)

from .console import build_bold_download_description, console
from .headers import resolve_header_format, sanitize_header
from .models import (
    BuildSource,
    CanonicalRecord,
    ResolvedTaxon,
    append_records_to_spool,
    build_source_merge_row,
    DEFAULT_BOLD_HEADER_FORMAT,
)

def process_bold_taxon_to_spool(
    resolved_taxon: ResolvedTaxon,
    prepared_query: Any,
    marker_keys: List[str],
    marker_map: Dict[str, Dict[str, Any]],
    output_cfg: Dict[str, Any],
    source: BuildSource,
    progress: Progress,
    bold_spool_f,
    lock: Lock,
    counters: Dict[str, int],
    source_merge_rows: List[Dict[str, str]],
    ncbi_accessions: set,
    spool_dir: Path,
    run_logger: logging.Logger,
) -> None:
    specimen_count = prepared_query.specimen_count
    if specimen_count == 0:
        run_logger.info(
            "# bold query:"
            f" taxon={resolved_taxon.scientific_name}"
            f" normalized={prepared_query.normalized_query}"
            f" specimens=0 downloaded=0 matched=0"
        )
        console.print(
            f"[yellow]BOLD {resolved_taxon.scientific_name}: 0 records "
            f"(NCBI scientific name mismatch may be involved)[/yellow]"
        )
        return

    if not prepared_query.query_id:
        raise typer.BadParameter("BOLD query preparation did not return a query_id.")

    download_ext = "json" if prepared_query.download_format == "json" else "tsv"
    query_hash = hashlib.sha1(prepared_query.query_id.encode("utf-8")).hexdigest()[:12]
    download_path = spool_dir / f"bold_{query_hash}.{download_ext}"
    task_id = progress.add_task(
        f"BOLD {resolved_taxon.scientific_name}: download",
        total=None,
    )
    last_download_report = {"bytes": 0}

    def on_bold_download_progress(downloaded_bytes: int, content_length: Optional[int]) -> None:
        threshold = 1024 * 1024
        if downloaded_bytes - last_download_report["bytes"] < threshold and downloaded_bytes != 0:
            return
        last_download_report["bytes"] = downloaded_bytes
        progress.update(
            task_id,
            description=build_bold_download_description(
                resolved_taxon.scientific_name,
                downloaded_bytes,
                content_length,
            ),
        )

    try:
        download_meta = download_documents_to_path(
            prepared_query.query_id,
            prepared_query.runtime_cfg,
            download_path,
            fmt=prepared_query.download_format,
            progress_callback=on_bold_download_progress,
        )

        downloaded_rows = 0
        matched_rows = 0
        buffered_records: List[CanonicalRecord] = []
        progress.update(
            task_id,
            description=f"BOLD {resolved_taxon.scientific_name}: filter",
            total=specimen_count,
            completed=0,
        )
        for row in iter_document_rows_from_path(download_path, prepared_query.download_format):
            downloaded_rows += 1
            counters["total_records"] += 1
            if specimen_count is not None and downloaded_rows > specimen_count:
                progress.update(task_id, total=downloaded_rows)
            progress.update(task_id, advance=1)

            normalized = normalize_bold_row(row, marker_keys, marker_map)
            if not normalized:
                continue

            matched_rows += 1
            counters["matched_records"] += 1
            counters["matched_features"] += 1

            record = build_bold_canonical_record(normalized, marker_map, output_cfg)
            accession_tokens = parse_accession_tokens(record.accession)
            if source == BuildSource.BOTH and accession_tokens and any(
                token in ncbi_accessions for token in accession_tokens
            ):
                record.linked_to_ncbi = True
                record.emitted_to_fasta = False
                record.skip_reason = "linked_by_insdcacs"
                source_merge_rows.append(build_source_merge_row(record))
                continue

            buffered_records.append(record)
            if len(buffered_records) >= 1000:
                append_records_to_spool(buffered_records, bold_spool_f, lock)
                buffered_records.clear()

        if buffered_records:
            append_records_to_spool(buffered_records, bold_spool_f, lock)

        run_logger.info(
            "# bold query:"
            f" taxon={resolved_taxon.scientific_name}"
            f" normalized={prepared_query.normalized_query}"
            f" specimens={specimen_count}"
            f" format={prepared_query.download_format}"
            f" bytes={download_meta.get('downloaded_bytes')}"
            f" downloaded={downloaded_rows}"
            f" matched={matched_rows}"
        )
    finally:
        progress.remove_task(task_id)
        download_path.unlink(missing_ok=True)




def build_bold_canonical_record(
    normalized_row: Dict[str, Any],
    marker_map: Dict[str, Dict],
    output_cfg: Dict,
) -> CanonicalRecord:
    marker_key = str(normalized_row["marker_key"])
    marker_cfg = marker_map[marker_key]
    header_key = marker_cfg.get("header_format")
    if not header_key:
        header_format = DEFAULT_BOLD_HEADER_FORMAT
    else:
        header_format = resolve_header_format(marker_cfg, output_cfg)
    processid = normalized_row.get("processid")
    sampleid = normalized_row.get("sampleid")
    accession = normalized_row.get("accession")
    taxon_name = normalized_row.get("taxon_name") or "unknown"
    source_record_id = str(normalized_row["source_record_id"])
    acc_id = f"BOLD_{sanitize_header(processid or source_record_id)}"
    marker_label = str(normalized_row.get("marker_label") or "")
    marker_safe = sanitize_header(marker_key)
    marker_label_safe = sanitize_header(marker_label or marker_key)
    organism_safe = sanitize_header(str(taxon_name))

    header_values = {
        "acc": str(accession or ""),
        "acc_id": acc_id,
        "db": "bold",
        "organism": organism_safe,
        "organism_raw": str(taxon_name),
        "marker": marker_safe,
        "marker_raw": marker_key,
        "label": marker_label_safe,
        "label_raw": marker_label,
        "type": "barcode",
        "type_raw": "barcode",
        "start": "",
        "end": "",
        "loc": "",
        "strand": "",
        "dup": "",
        "source": BuildSource.BOLD.value,
        "source_id": source_record_id,
    }
    return CanonicalRecord(
        source=BuildSource.BOLD.value,
        source_record_id=source_record_id,
        accession=str(accession or "") or None,
        processid=str(processid or "") or None,
        sampleid=str(sampleid or "") or None,
        taxon_name=str(taxon_name),
        marker_key=marker_key,
        marker_label=marker_label,
        sequence=str(normalized_row["sequence"]),
        header_values=header_values,
        metadata={
            "header_format": header_format,
            "raw_row_json": json.dumps(normalized_row.get("raw_row") or {}, ensure_ascii=False),
        },
    )




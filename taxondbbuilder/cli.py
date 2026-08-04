"""Typer application and build orchestration."""

import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import csv
import hashlib
import shutil
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date, datetime
from http import HTTPStatus
from http.client import HTTPException, RemoteDisconnected
from pathlib import Path
from queue import Queue
from string import Formatter
from threading import Event, Lock, Thread
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError

import typer
from Bio import Entrez, SeqIO
from Bio.Seq import Seq
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .models import *
from .console import *
from .logging_utils import *
from .headers import *
from .markers import *
from .config import *
from .fasta import *
from .postprep.length_filter import *
from .postprep.primer_trim import *
from .postprep.duplicates import *
from .ncbi import *
from .bold import *

app = typer.Typer(
    add_completion=False,
    help="TaxonDBBuilder - build a generic NCBI FASTA database by taxon and marker.",
)

def build_output_path(
    out: Optional[Path],
    taxids: List[str],
    markers: List[str],
    output_prefix: str = "",
) -> Path:
    run_date = datetime.now().strftime("%Y%m%d")
    if out:
        if out.suffix in {".fa", ".fasta", ".fas"}:
            out.parent.mkdir(parents=True, exist_ok=True)
            return out
        out_dir = out
    else:
        out_dir = Path("Results") / "db" / run_date

    out_dir.mkdir(parents=True, exist_ok=True)
    taxon_label = f"taxid{'+'.join(taxids)}" if len(taxids) == 1 else "multi_taxon"
    marker_label = "+".join(markers) if len(markers) == 1 else "multi_marker"
    prefix = output_prefix
    if prefix and not prefix.endswith("_"):
        prefix = prefix + "_"
    return out_dir / f"{prefix}{taxon_label}__{marker_label}.fasta"



@app.command("list-markers")
def list_markers(
    config: Path = typer.Option(..., "--config", "-c", help="Path to TOML config file."),
) -> None:
    """
    List marker IDs and aliases from the config (including markers.file).
    """
    cfg = load_config(config)
    markers = cfg.get("markers", {})
    table = Table(title="Markers", show_header=True, header_style="bold")
    table.add_column("Marker ID")
    table.add_column("Aliases")
    table.add_column("Header Format")
    for key in sorted(markers.keys()):
        entry = markers[key] or {}
        aliases = entry.get("aliases") or []
        header_format = entry.get("header_format") or ""
        aliases_text = ", ".join(aliases) if aliases else "-"
        table.add_row(str(key), aliases_text, str(header_format) or "-")
    console.print(table)


@app.command("list-primer-sets")
def list_primer_sets(
    config: Path = typer.Option(..., "--config", "-c", help="Path to TOML config file."),
) -> None:
    """
    List primer set IDs from [post_prep].primer_file.
    """
    if not config.exists():
        raise typer.BadParameter(f"Config file not found: {config}")
    with config.open("rb") as f:
        cfg = tomllib.load(f)

    post_prep_cfg = cfg.get("post_prep") or {}
    if not isinstance(post_prep_cfg, dict):
        raise typer.BadParameter("[post_prep] must be a table (dict).")
    primer_file = post_prep_cfg.get("primer_file")
    if not primer_file:
        raise typer.BadParameter(
            "[post_prep].primer_file is not set in config. Set it to use list-primer-sets."
        )
    primer_path = resolve_support_file_path(str(primer_file), config, "Primer file")
    primer_sets = load_primer_sets_from_file(primer_path)

    table = Table(title=f"Primer Sets ({primer_path})", show_header=True, header_style="bold")
    table.add_column("Primer Set")
    table.add_column("Forward")
    table.add_column("Reverse")
    for key in sorted(primer_sets.keys()):
        entry = primer_sets[key]
        table.add_row(key, str(len(entry["forward"])), str(len(entry["reverse"])))
    console.print(table)


@app.command()
def build(
    config: Path = typer.Option(..., "--config", "-c", help="Path to TOML config file."),
    taxon: List[str] = typer.Option(..., "--taxon", "-t", help="Taxon (taxid or scientific name)."),
    marker: List[str] = typer.Option(..., "--marker", "-m", help="Marker key or prefix."),
    source: BuildSource = typer.Option(
        BuildSource.NCBI,
        "--source",
        help="Sequence source: ncbi, bold, or both.",
    ),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output file or directory."),
    dump_gb: Optional[Path] = typer.Option(
        None,
        "--dump-gb",
        help="Directory to store GenBank chunks for caching.",
    ),
    from_gb: Optional[Path] = typer.Option(
        None,
        "--from-gb",
        help="Directory of GenBank chunks to extract without downloading.",
    ),
    resume: bool = typer.Option(False, "--resume", help="Resume using cached GenBank chunks."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print query and exit."),
    workers: int = typer.Option(2, "--workers", "-w", help="Number of extraction workers."),
    output_prefix: str = typer.Option(
        "taxondbbuilder_",
        "--output-prefix",
        help="Prefix added to output FASTA filename.",
    ),
    post_prep: bool = typer.Option(
        False,
        "--post-prep",
        help="Apply [post_prep] FASTA processing after extraction.",
    ),
    post_prep_step: Optional[List[PostPrepStep]] = typer.Option(
        None,
        "--post-prep-step",
        help=(
            "Post-prep step(s) to run. Repeat to select multiple. "
            "Choices: primer_trim, length_filter, duplicate_report."
        ),
    ),
    post_prep_primer_set: Optional[List[str]] = typer.Option(
        None,
        "--post-prep-primer-set",
        help="Primer set name(s) for primer_trim. Repeat to select multiple (overrides config).",
    ),
):
    """
    Build a FASTA database by downloading GenBank records and extracting features.

    Examples (Teleostomi):
      taxondbbuilder.py build -c configs/db.toml -t 117570 -m 12s
      taxondbbuilder.py build -c configs/db.toml -t "Salmo salar" -m mitogenome
      taxondbbuilder.py build -c configs/db.toml -t 117570 -m 12s --workers 2
      taxondbbuilder.py build -c configs/db.toml -t 117570 -m 12s --output-prefix "mifish"
      taxondbbuilder.py build -c configs/db.toml -t 117570 -m 12s --dump-gb Results/gb --resume
      taxondbbuilder.py build -c configs/db.toml -t 117570 -m 12s --from-gb Results/gb
      taxondbbuilder.py build -c configs/db.toml -t 117570 -m coi --source ncbi
    """
    cfg = load_config(config, source=source)
    ncbi_cfg = cfg.get("ncbi", {})
    filters_cfg = cfg.get("filters", {})
    output_cfg = cfg.get("output", {})
    post_prep_cfg = cfg.get("post_prep") or {}
    taxon_noexp = bool(cfg.get("taxon", {}).get("noexp", False))
    uses_ncbi = source in {BuildSource.NCBI, BuildSource.BOTH}
    uses_bold = source in {BuildSource.BOLD, BuildSource.BOTH}

    needs_entrez = uses_ncbi or uses_bold
    if needs_entrez:
        setup_entrez(ncbi_cfg if isinstance(ncbi_cfg, dict) else {}, warn_if_missing=uses_ncbi)
    marker_map = normalize_marker_map(cfg.get("markers", {}), source=source)

    marker_keys = [resolve_marker_key(m, marker_map) for m in marker]
    marker_query = build_marker_query(marker_keys, marker_map) if uses_ncbi else ""
    output_prefix = output_prefix.strip()
    selected_header_formats: List[str] = []
    marker_rules = []
    for key in marker_keys:
        cfg_m = marker_map[key]
        header_format = resolve_header_format(cfg_m, output_cfg)
        selected_header_formats.append(header_format)
        if not uses_ncbi:
            continue

        region_patterns = build_region_patterns(cfg_m)
        if not region_patterns:
            raise typer.BadParameter(f"markers.{key} has no patterns for region extraction.")
        compiled = compile_patterns(region_patterns)
        if not compiled:
            raise typer.BadParameter(f"markers.{key} patterns did not compile.")

        feature_types = cfg_m.get("feature_types")
        if feature_types is None:
            feature_types = DEFAULT_FEATURE_TYPES
        elif not feature_types:
            feature_types = None

        feature_fields = cfg_m.get("feature_fields")
        if feature_fields is None:
            feature_fields = DEFAULT_FEATURE_FIELDS
        elif not feature_fields:
            raise typer.BadParameter(f"markers.{key}.feature_fields cannot be empty.")

        marker_rules.append(
            {
                "key": key,
                "patterns": compiled,
                "feature_types": feature_types,
                "feature_fields": feature_fields,
                "header_format": header_format,
            }
        )

    requested_post_prep_steps = [step.value for step in (post_prep_step or [])]
    requested_primer_sets: List[str] = []
    for value in (post_prep_primer_set or []):
        name = value.strip()
        if not name:
            raise typer.BadParameter("--post-prep-primer-set cannot include empty values.")
        if name not in requested_primer_sets:
            requested_primer_sets.append(name)

    if post_prep:
        post_min = post_prep_cfg.get("sequence_length_min")
        post_max = post_prep_cfg.get("sequence_length_max")
        has_length_filter = post_min is not None or post_max is not None
        post_primer_forward = list(post_prep_cfg.get("_primer_forward") or [])
        post_primer_reverse = list(post_prep_cfg.get("_primer_reverse") or [])
        post_primer_file = post_prep_cfg.get("_primer_file_resolved") or post_prep_cfg.get("primer_file")
        post_primer_set_names = list(post_prep_cfg.get("_primer_set_names") or [])
        if not post_primer_set_names:
            primer_set_cfg = post_prep_cfg.get("primer_set")
            if isinstance(primer_set_cfg, str) and primer_set_cfg.strip():
                post_primer_set_names = [primer_set_cfg.strip()]
            elif isinstance(primer_set_cfg, list):
                post_primer_set_names = [str(v).strip() for v in primer_set_cfg if str(v).strip()]

        if requested_primer_sets:
            if not post_primer_file:
                raise typer.BadParameter(
                    "--post-prep-primer-set requires [post_prep].primer_file in config."
                )
            primer_path = resolve_support_file_path(str(post_primer_file), config, "Primer file")
            primer_sets_data = load_primer_sets_from_file(primer_path)
            post_primer_forward, post_primer_reverse = combine_primer_set_sequences(
                primer_sets_data, requested_primer_sets
            )
            post_primer_file = str(primer_path)
            post_primer_set_names = requested_primer_sets

        has_primer_trim = bool(post_primer_forward and post_primer_reverse)
        post_primer_trim_options: Dict[str, Any] = {
            "trim_mode": post_prep_cfg.get("primer_trim_mode", PRIMER_TRIM_MODE_ONE_OR_BOTH),
            "max_mismatch": int(post_prep_cfg.get("primer_max_mismatch", 0)),
            "max_error_rate": float(post_prep_cfg.get("primer_max_error_rate", 0.0)),
            "min_overlap_bp": post_prep_cfg.get("primer_min_overlap_bp"),
            "min_overlap_ratio": float(post_prep_cfg.get("primer_min_overlap_ratio", 1.0)),
            "end_max_offset": int(post_prep_cfg.get("primer_end_max_offset", 0)),
            "keep_retained_fasta": bool(post_prep_cfg.get("primer_keep_retained_fasta", True)),
            "iter_enable": bool(post_prep_cfg.get("primer_iter_enable", False)),
            "iter_max_rounds": int(post_prep_cfg.get("primer_iter_max_rounds", 3)),
            "iter_stop_delta": float(post_prep_cfg.get("primer_iter_stop_delta", 0.002)),
            "iter_target_conf": float(post_prep_cfg.get("primer_iter_target_conf", 0.98)),
            "sidecar_format": post_prep_cfg.get("primer_sidecar_format", "tsv"),
            "recheck_tool": post_prep_cfg.get("primer_recheck_tool", "off"),
            "recheck_min_identity": float(post_prep_cfg.get("primer_recheck_min_identity", 0.85)),
            "recheck_min_query_cov": float(post_prep_cfg.get("primer_recheck_min_query_cov", 0.7)),
            "phylo_target_confidence": post_prep_cfg.get("primer_phylo_target_confidence", "medium"),
        }

        if PostPrepStep.PRIMER_TRIM.value in requested_post_prep_steps and not has_primer_trim:
            raise typer.BadParameter(
                "post-prep step 'primer_trim' requires post_prep.primer_file and post_prep.primer_set."
            )
        if PostPrepStep.LENGTH_FILTER.value in requested_post_prep_steps and not has_length_filter:
            raise typer.BadParameter(
                "post-prep step 'length_filter' requires post_prep.sequence_length_min or post_prep.sequence_length_max."
            )

        if requested_post_prep_steps:
            post_prep_steps_run = [
                step for step in POST_PREP_STEP_ORDER if step in requested_post_prep_steps
            ]
        else:
            post_prep_steps_run = []
            if has_primer_trim:
                post_prep_steps_run.append(PostPrepStep.PRIMER_TRIM.value)
            if has_length_filter:
                post_prep_steps_run.append(PostPrepStep.LENGTH_FILTER.value)
            if source != BuildSource.BOTH:
                post_prep_steps_run.append(PostPrepStep.DUPLICATE_REPORT.value)

        post_min = int(post_min) if post_min is not None else None
        post_max = int(post_max) if post_max is not None else None
    else:
        if requested_post_prep_steps or requested_primer_sets:
            raise typer.BadParameter("--post-prep-step/--post-prep-primer-set requires --post-prep.")
        has_length_filter = False
        has_primer_trim = False
        post_prep_steps_run = []
        post_min = None
        post_max = None
        post_primer_forward = []
        post_primer_reverse = []
        post_primer_file = None
        post_primer_set_names = []
        post_primer_trim_options = {
            "trim_mode": PRIMER_TRIM_MODE_ONE_OR_BOTH,
            "max_mismatch": 0,
            "max_error_rate": 0.0,
            "min_overlap_bp": None,
            "min_overlap_ratio": 1.0,
            "end_max_offset": 0,
            "keep_retained_fasta": True,
            "iter_enable": False,
            "iter_max_rounds": 1,
            "iter_stop_delta": 0.002,
            "iter_target_conf": 0.98,
            "sidecar_format": "tsv",
            "recheck_tool": "off",
            "recheck_min_identity": 0.85,
            "recheck_min_query_cov": 0.7,
            "phylo_target_confidence": "medium",
        }

    resolved_taxa: List[ResolvedTaxon] = []
    warnings: List[str] = []
    for t in taxon:
        resolved = resolve_taxon(t, require_scientific_name=uses_bold)
        resolved_taxa.append(resolved)
        if resolved.warning:
            warnings.append(resolved.warning)

    taxids = [item.taxid for item in resolved_taxa]

    out_path = build_output_path(out, taxids, marker_keys, output_prefix=output_prefix)
    log_path = out_path.with_suffix(out_path.suffix + ".log")
    if source == BuildSource.BOLD and (dump_gb or from_gb or resume):
        raise typer.BadParameter("--dump-gb, --from-gb, and --resume are not supported with --source bold.")
    if resume and not dump_gb and not from_gb:
        raise typer.BadParameter("--resume requires --dump-gb or --from-gb.")
    if from_gb and not from_gb.exists():
        raise typer.BadParameter(f"--from-gb not found: {from_gb}")
    if dump_gb:
        dump_gb.mkdir(parents=True, exist_ok=True)

    run_logger = setup_run_logger(log_path)
    try:
        with tee_console_output(log_path):
            print_header()
            render_run_table(
                config,
                source,
                taxids,
                marker_keys,
                out_path,
                filters_cfg,
                dump_gb=dump_gb,
                from_gb=from_gb,
                resume=resume,
            )
            for w in warnings:
                console.print(f"[yellow]WARNING:[/yellow] {w}")

            if dry_run:
                if uses_ncbi:
                    for taxid in taxids:
                        query = build_query(taxid, marker_query, filters_cfg, taxon_noexp)
                        console.print(query)
                if uses_bold:
                    for item in resolved_taxa:
                        console.print(f"BOLD query taxon: {item.scientific_name}")
                return

            acc_to_seqs: Dict[str, set] = {}
            counters = {
                "total_records": 0,
                "matched_records": 0,
                "matched_features": 0,
                "kept_records": 0,
                "skipped_same": 0,
                "duplicated_diff": 0,
            }
            dup_accessions: Dict[str, int] = {}
            emitted_records: List[Dict[str, str]] = []
            source_merge_rows: List[Dict[str, str]] = []

            progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
                disable=not console.is_terminal,
            )

            run_logger.info(f"# started: {datetime.now().isoformat()}")
            run_logger.info(f"# config: {config}")
            run_logger.info(f"# source: {source.value}")
            run_logger.info(f"# taxon input: {taxon}")
            run_logger.info(f"# taxids: {taxids}")
            run_logger.info(f"# scientific_names: {[item.scientific_name for item in resolved_taxa]}")
            run_logger.info(f"# markers: {marker_keys}")
            run_logger.info(f"# output_prefix: {output_prefix}")
            run_logger.info(f"# dump_gb: {dump_gb}" if dump_gb else "# dump_gb: none")
            run_logger.info(f"# from_gb: {from_gb}" if from_gb else "# from_gb: none")
            run_logger.info(f"# resume: {resume}")
            run_logger.info(f"# post_prep: {post_prep}")
            if post_prep:
                steps_text = ", ".join(post_prep_steps_run) if post_prep_steps_run else "none"
                run_logger.info(f"# post_prep.steps: {steps_text}")
            if post_prep and has_length_filter:
                if post_min is not None:
                    run_logger.info(f"# post_prep.sequence_length_min: {post_min}")
                if post_max is not None:
                    run_logger.info(f"# post_prep.sequence_length_max: {post_max}")
            if post_prep and has_primer_trim:
                run_logger.info(f"# post_prep.primer_file: {post_primer_file}")
                run_logger.info(f"# post_prep.primer_set: {', '.join(post_primer_set_names)}")
                run_logger.info(f"# post_prep.primer_forward_count: {len(post_primer_forward)}")
                run_logger.info(f"# post_prep.primer_reverse_count: {len(post_primer_reverse)}")
                run_logger.info(f"# post_prep.primer_trim_mode: {post_primer_trim_options['trim_mode']}")
                run_logger.info(f"# post_prep.primer_max_mismatch: {post_primer_trim_options['max_mismatch']}")
                run_logger.info(f"# post_prep.primer_max_error_rate: {post_primer_trim_options['max_error_rate']}")
                run_logger.info(f"# post_prep.primer_min_overlap_bp: {post_primer_trim_options['min_overlap_bp']}")
                run_logger.info(
                    f"# post_prep.primer_min_overlap_ratio: {post_primer_trim_options['min_overlap_ratio']}"
                )
                run_logger.info(f"# post_prep.primer_end_max_offset: {post_primer_trim_options['end_max_offset']}")
                run_logger.info(f"# post_prep.primer_iter_enable: {post_primer_trim_options['iter_enable']}")
                run_logger.info(f"# post_prep.primer_iter_max_rounds: {post_primer_trim_options['iter_max_rounds']}")
                run_logger.info(f"# post_prep.primer_sidecar_format: {post_primer_trim_options['sidecar_format']}")
                run_logger.info(f"# post_prep.primer_recheck_tool: {post_primer_trim_options['recheck_tool']}")
                run_logger.info(
                    f"# post_prep.primer_recheck_min_identity: {post_primer_trim_options['recheck_min_identity']}"
                )
                run_logger.info(
                    f"# post_prep.primer_recheck_min_query_cov: {post_primer_trim_options['recheck_min_query_cov']}"
                )
            if warnings:
                run_logger.info("# warnings:")
                for warning in warnings:
                    run_logger.info(f"# - {warning}")

            lock = Lock()
            run_logger.info(f"# workers: {workers}")

            with tempfile.TemporaryDirectory(prefix="taxondbbuilder_spool_") as spool_dir_name:
                spool_dir = Path(spool_dir_name)
                ncbi_spool_path = spool_dir / "ncbi_records.jsonl"
                bold_spool_path = spool_dir / "bold_records.jsonl"

                with ncbi_spool_path.open("w", encoding="utf-8") as ncbi_spool_f, bold_spool_path.open(
                    "w", encoding="utf-8"
                ) as bold_spool_f, progress:
                    if uses_ncbi:
                        for taxid in taxids:
                            query = build_query(taxid, marker_query, filters_cfg, taxon_noexp)
                            run_logger.info(f"# query taxid={taxid}: {query}")
                            delay_sec = default_delay(ncbi_cfg)
                            if from_gb:
                                data_iter = iter_genbank_files(from_gb, taxid)
                                count = None
                                run_logger.info(f"# query count taxid={taxid}: from-gb")
                            else:
                                count, data_iter = fetch_genbank(
                                    query,
                                    ncbi_cfg,
                                    delay_sec,
                                    dump_dir=dump_gb,
                                    resume=resume,
                                    taxid=taxid,
                                )
                                run_logger.info(f"# query count taxid={taxid}: {count}")
                                if count == 0:
                                    console.print(f"[yellow]taxid {taxid}: 0 records[/yellow]")
                                    continue
                                run_logger.info(f"# fetch progress taxid={taxid}: 0/{count}")

                            task_id = progress.add_task(f"taxid {taxid}", total=count)
                            if workers < 1:
                                raise typer.BadParameter("--workers must be >= 1.")

                            q: Queue = Queue(maxsize=max(1, workers * 2))
                            stop_event = Event()
                            errors: List[Exception] = []

                            def worker() -> None:
                                while True:
                                    item = q.get()
                                    if item is None:
                                        q.task_done()
                                        break
                                    try:
                                        start, chunk = item
                                        records = extract_ncbi_records_from_genbank_chunk(
                                            chunk,
                                            marker_rules,
                                            acc_to_seqs,
                                            counters,
                                            dup_accessions,
                                            lock,
                                            progress,
                                            task_id,
                                            taxid,
                                            dump_gb,
                                        )
                                        append_records_to_spool(records, ncbi_spool_f, lock)
                                    except Exception as exc:
                                        errors.append(exc)
                                        stop_event.set()
                                    finally:
                                        q.task_done()

                            threads = [Thread(target=worker, daemon=True) for _ in range(workers)]
                            for t in threads:
                                t.start()

                            for start, chunk in data_iter:
                                if stop_event.is_set():
                                    break
                                if not chunk:
                                    continue
                                if count is not None and count > 0:
                                    per_query = int(ncbi_cfg.get("per_query", 100))
                                    fetched = min(start + per_query, count)
                                    run_logger.info(f"# fetch progress taxid={taxid}: {fetched}/{count}")
                                q.put((start, chunk))

                            for _ in threads:
                                q.put(None)
                            q.join()
                            for t in threads:
                                t.join()
                            if errors:
                                raise errors[0]

                    if uses_bold:
                        ncbi_accessions = set(acc_to_seqs.keys())
                        for resolved_taxon in resolved_taxa:
                            try:
                                prepared_query = prepare_bold_query(
                                    resolved_taxon.scientific_name,
                                    cfg.get("bold"),
                                )
                                process_bold_taxon_to_spool(
                                    resolved_taxon,
                                    prepared_query,
                                    marker_keys,
                                    marker_map,
                                    output_cfg,
                                    source,
                                    progress,
                                    bold_spool_f,
                                    lock,
                                    counters,
                                    source_merge_rows,
                                    ncbi_accessions,
                                    spool_dir,
                                    run_logger,
                                )
                            except BoldApiError as exc:
                                raise typer.BadParameter(str(exc)) from exc

                ncbi_records = load_records_from_spool(ncbi_spool_path)
                bold_records = load_records_from_spool(bold_spool_path)
                ncbi_records.sort(key=canonical_record_sort_key)
                bold_records.sort(key=canonical_record_sort_key)

                with out_path.open("w", encoding="utf-8") as out_f:
                    if ncbi_records:
                        emit_records_to_fasta(
                            ncbi_records,
                            out_f,
                            counters,
                            emitted_records,
                            lock,
                            source_merge_rows=source_merge_rows,
                        )
                    if bold_records:
                        emit_records_to_fasta(
                            bold_records,
                            out_f,
                            counters,
                            emitted_records,
                            lock,
                            source_merge_rows=source_merge_rows,
                        )

            duplicate_records_report_path: Optional[Path] = None
            duplicate_groups_report_path: Optional[Path] = None
            source_merge_path = write_source_merge_csv(out_path, source_merge_rows)
            run_logger.info(f"# source_merge_csv: {source_merge_path}")
            if post_prep:
                before_post_prep = counters["kept_records"]
                run_logger.info(f"# kept records before post_prep: {before_post_prep}")

                if PostPrepStep.PRIMER_TRIM.value in post_prep_steps_run:
                    primer_stats = apply_post_prep_primer_trim(
                        out_path,
                        post_primer_forward,
                        post_primer_reverse,
                        options=post_primer_trim_options,
                    )
                    counters["kept_records"] = primer_stats["after"]
                    run_logger.info(
                        "# post_prep primer trim:"
                        f" before={primer_stats['before']} after={primer_stats['after']}"
                        f" removed={primer_stats['removed']} trimmed_both={primer_stats['trimmed_both']}"
                        f" trimmed_left_only={primer_stats['trimmed_left_only']}"
                        f" trimmed_right_only={primer_stats['trimmed_right_only']}"
                        f" untrimmed={primer_stats['untrimmed']}"
                        f" dropped_empty={primer_stats['dropped_empty']}"
                        f" canonical_orientation={primer_stats['canonical_orientation']}"
                        f" reverse_orientation={primer_stats['reverse_orientation']}"
                        f" confidence_high={primer_stats['confidence_high']}"
                        f" confidence_medium={primer_stats['confidence_medium']}"
                        f" confidence_low={primer_stats['confidence_low']}"
                        f" rounds_run={primer_stats['rounds_run']}"
                        f" best_round={primer_stats['best_round']}"
                        f" high_conf_rate={primer_stats['high_conf_rate']:.4f}"
                    )
                    if primer_stats.get("sidecar_path"):
                        run_logger.info(f"# post_prep primer sidecar: {primer_stats['sidecar_path']}")
                    if primer_stats.get("retained_path"):
                        run_logger.info(f"# post_prep primer retained_fasta: {primer_stats['retained_path']}")
                    run_logger.info(
                        "# post_prep primer recheck:"
                        f" tool={primer_stats.get('recheck_tool', 'off')}"
                        f" attempted={primer_stats.get('recheck_attempted', 0)}"
                        f" rescued={primer_stats.get('recheck_rescued', 0)}"
                        f" error={primer_stats.get('recheck_error') or 'none'}"
                    )

                if PostPrepStep.LENGTH_FILTER.value in post_prep_steps_run:
                    length_stats = apply_post_prep_length_filter(out_path, post_min, post_max)
                    counters["kept_records"] = length_stats["after"]
                    run_logger.info(
                        "# post_prep length filter:"
                        f" before={length_stats['before']} after={length_stats['after']}"
                        f" removed={length_stats['removed']}"
                    )

                if PostPrepStep.DUPLICATE_REPORT.value in post_prep_steps_run:
                    (
                        duplicate_records_report_path,
                        duplicate_groups_report_path,
                        dup_stats,
                        dup_reason,
                    ) = write_duplicate_acc_reports_csv(out_path, selected_header_formats)
                    if dup_reason:
                        run_logger.info(f"# post_prep duplicate_acc_report: skipped ({dup_reason})")
                        console.print(f"[yellow]post_prep:[/yellow] duplicate ACC report skipped ({dup_reason}).")
                    else:
                        run_logger.info(
                            "# post_prep duplicate_acc_report:"
                            f" total={dup_stats['total_records']} parsed={dup_stats['parsed_records']}"
                            f" unparsed={dup_stats['unparsed_records']} groups={dup_stats['duplicate_groups']}"
                            f" records={dup_stats['duplicate_records']}"
                            f" cross_organism_groups={dup_stats['cross_organism_groups']}"
                        )
                        if duplicate_records_report_path:
                            run_logger.info(f"# post_prep duplicate_acc_records_csv: {duplicate_records_report_path}")
                            console.print(f"post_prep duplicate ACC records CSV: {duplicate_records_report_path}")
                        if duplicate_groups_report_path:
                            run_logger.info(f"# post_prep duplicate_acc_groups_csv: {duplicate_groups_report_path}")
                            console.print(f"post_prep duplicate ACC groups CSV: {duplicate_groups_report_path}")
                else:
                    run_logger.info("# post_prep duplicate_acc_report: skipped (step disabled)")

            acc_species_map_path, acc_species_stats = write_acc_organism_mapping_csv(out_path, emitted_records)
            run_logger.info(
                "# acc_organism_map:"
                f" total={acc_species_stats['total_records']} mapped={acc_species_stats['mapped_records']}"
                f" unmapped={acc_species_stats['unmapped_records']}"
                f" unused_source_records={acc_species_stats['unused_records']}"
                f" unique_accessions={acc_species_stats['unique_accessions']}"
                f" unique_organisms={acc_species_stats['unique_organisms']}"
            )
            run_logger.info(f"# acc_organism_map_csv: {acc_species_map_path}")

            run_logger.info(f"# total records: {counters['total_records']}")
            run_logger.info(f"# matched records: {counters['matched_records']}")
            run_logger.info(f"# matched features: {counters['matched_features']}")
            run_logger.info(f"# kept records: {counters['kept_records']}")
            run_logger.info(f"# skipped duplicates (same accession+sequence): {counters['skipped_same']}")
            run_logger.info(f"# kept duplicates (same accession, different sequence): {counters['duplicated_diff']}")
            if dup_accessions:
                run_logger.info("# duplicate accessions with different sequences:")
                for acc, count in sorted(dup_accessions.items()):
                    run_logger.info(f"# - {acc}: {count} sequences")
            run_logger.info(f"# output: {out_path}")
            run_logger.info(f"# finished: {datetime.now().isoformat()}")

            render_result_table(
                counters["total_records"],
                counters["matched_records"],
                counters["matched_features"],
                counters["kept_records"],
                counters["skipped_same"],
                counters["duplicated_diff"],
                out_path,
                log_path,
            )
            if dup_accessions:
                console.print(
                    "[yellow]WARNING:[/yellow] duplicate accessions with different sequences were kept. See log for details."
                )
            console.print(f"ACC-organism mapping CSV: {acc_species_map_path}")
            console.print(f"Source merge CSV: {source_merge_path}")
            if acc_species_stats["unmapped_records"] > 0:
                console.print(
                    "[yellow]WARNING:[/yellow] Some final FASTA records could not be mapped to source ACC/organism. "
                    "See .log for details."
                )
    finally:
        close_run_logger(run_logger)





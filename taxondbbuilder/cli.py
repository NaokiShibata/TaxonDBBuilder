"""Typer application and build orchestration."""

import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread
from typing import Any

import typer
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .bold import process_bold_taxon_to_spool
from .config import (
    BUILTIN_PRIMER_SETS,
    combine_primer_set_sequences,
    load_config,
    load_primer_sets_from_file,
    resolve_support_file_path,
    tomllib,
)
from .console import console, print_header, render_result_table, render_run_table
from .fasta import emit_records_to_fasta
from .headers import resolve_header_format
from .logging_utils import (
    close_run_logger,
    setup_run_logger,
    tee_console_output,
)
from .markers import (
    build_marker_query,
    build_region_patterns,
    compile_patterns,
    normalize_marker_map,
    resolve_marker_key,
)
from .models import (
    DEFAULT_FEATURE_FIELDS,
    DEFAULT_FEATURE_TYPES,
    POST_PREP_STEP_ORDER,
    PRIMER_TRIM_MODE_ONE_OR_BOTH,
    BuildSource,
    PostPrepStep,
    ResolvedTaxon,
    append_records_to_spool,
    canonical_record_sort_key,
    load_records_from_spool,
    write_source_merge_csv,
)
from .ncbi import (
    build_query,
    default_delay,
    extract_ncbi_records_from_genbank_chunk,
    fetch_genbank,
    iter_genbank_files,
    resolve_taxon,
    setup_entrez,
)
from .bold_api import (
    BoldApiError,
    prepare_bold_query,
)
from .postprep.duplicates import (
    write_acc_organism_mapping_csv,
    write_duplicate_acc_reports_csv,
)
from .postprep.length_filter import apply_post_prep_length_filter
from .postprep.phylo import apply_post_prep_msa_tree
from .postprep.primer_trim import apply_post_prep_primer_trim

app = typer.Typer(
    add_completion=False,
    help="TaxonDBBuilder - build a generic NCBI FASTA database by taxon and marker.",
)


@dataclass
class _BuildContext:
    cfg: dict[str, Any]
    config: Path
    source: BuildSource
    taxon: list[str]
    taxids: list[str]
    resolved_taxa: list[ResolvedTaxon]
    marker_keys: list[str]
    marker_query: str
    marker_rules: list[dict[str, Any]]
    marker_map: dict[str, dict[str, Any]]
    output_cfg: dict[str, Any]
    selected_header_formats: list[str]
    ncbi_cfg: dict[str, Any]
    filters_cfg: dict[str, Any]
    taxon_noexp: bool
    output_prefix: str
    out_path: Path
    log_path: Path
    dump_gb: Path | None
    from_gb: Path | None
    resume: bool
    dry_run: bool
    workers: int
    uses_ncbi: bool
    uses_bold: bool
    warnings: list[str]
    post_prep: bool
    post_prep_steps_run: list[str]
    has_length_filter: bool
    has_primer_trim: bool
    has_msa_tree: bool
    post_min: int | None
    post_max: int | None
    post_primer_forward: list[str]
    post_primer_reverse: list[str]
    post_primer_file: str | None
    post_primer_set_names: list[str]
    post_primer_trim_options: dict[str, Any]
    post_msa_tree_options: dict[str, Any]
    run_logger: Any = None
    lock: Lock = field(default_factory=Lock)
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
    acc_to_seqs: dict[str, set] = field(default_factory=dict)
    dup_accessions: dict[str, int] = field(default_factory=dict)
    emitted_records: list[dict[str, str]] = field(default_factory=list)
    source_merge_rows: list[dict[str, str]] = field(default_factory=list)
    progress: Progress | None = None
    msa_tree_stats: dict[str, Any] | None = None


def build_output_path(
    out: Path | None,
    taxids: list[str],
    markers: list[str],
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


def _build_marker_rules(
    marker_keys: list[str],
    marker_map: dict[str, dict[str, Any]],
    output_cfg: dict[str, Any],
    uses_ncbi: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    selected_header_formats = _collect_marker_header_formats(
        marker_keys, marker_map, output_cfg
    )
    marker_rules = []
    if uses_ncbi:
        marker_rules = [
            _build_ncbi_marker_rule(key, marker_map[key], header_format)
            for key, header_format in zip(marker_keys, selected_header_formats)
        ]
    return selected_header_formats, marker_rules


def _collect_marker_header_formats(
    marker_keys: list[str],
    marker_map: dict[str, dict[str, Any]],
    output_cfg: dict[str, Any],
) -> list[str]:
    return [resolve_header_format(marker_map[key], output_cfg) for key in marker_keys]


def _build_ncbi_marker_rule(
    key: str, cfg_m: dict[str, Any], header_format: str
) -> dict[str, Any]:
    region_patterns = build_region_patterns(cfg_m)
    if not region_patterns:
        raise typer.BadParameter(
            f"markers.{key} has no patterns for region extraction."
        )
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
    return {
        "key": key,
        "patterns": compiled,
        "feature_types": feature_types,
        "feature_fields": feature_fields,
        "header_format": header_format,
    }


def _show_build_plan(ctx: _BuildContext) -> bool:
    print_header()
    render_run_table(
        ctx.config,
        ctx.source,
        ctx.taxids,
        ctx.marker_keys,
        ctx.out_path,
        ctx.filters_cfg,
        dump_gb=ctx.dump_gb,
        from_gb=ctx.from_gb,
        resume=ctx.resume,
    )
    for warning in ctx.warnings:
        console.print(f"[yellow]WARNING:[/yellow] {warning}")
    if not ctx.dry_run:
        return False
    if ctx.uses_ncbi:
        for taxid in ctx.taxids:
            query = build_query(
                taxid, ctx.marker_query, ctx.filters_cfg, ctx.taxon_noexp
            )
            console.print(query)
    if ctx.uses_bold:
        for item in ctx.resolved_taxa:
            console.print(f"BOLD query taxon: {item.scientific_name}")
    return True


def _log_build_inputs(ctx: _BuildContext) -> None:
    logger = ctx.run_logger
    logger.info(f"# started: {datetime.now().isoformat()}")
    logger.info(f"# config: {ctx.config}")
    logger.info(f"# source: {ctx.source.value}")
    logger.info(f"# taxon input: {ctx.taxon}")
    logger.info(f"# taxids: {ctx.taxids}")
    logger.info(
        f"# scientific_names: {[item.scientific_name for item in ctx.resolved_taxa]}"
    )
    logger.info(f"# markers: {ctx.marker_keys}")
    logger.info(f"# output_prefix: {ctx.output_prefix}")
    logger.info(f"# dump_gb: {ctx.dump_gb}" if ctx.dump_gb else "# dump_gb: none")
    logger.info(f"# from_gb: {ctx.from_gb}" if ctx.from_gb else "# from_gb: none")
    logger.info(f"# resume: {ctx.resume}")
    logger.info(f"# post_prep: {ctx.post_prep}")
    if ctx.post_prep:
        steps_text = (
            ", ".join(ctx.post_prep_steps_run) if ctx.post_prep_steps_run else "none"
        )
        logger.info(f"# post_prep.steps: {steps_text}")
    if ctx.post_prep and ctx.has_length_filter:
        if ctx.post_min is not None:
            logger.info(f"# post_prep.sequence_length_min: {ctx.post_min}")
        if ctx.post_max is not None:
            logger.info(f"# post_prep.sequence_length_max: {ctx.post_max}")
    if ctx.post_prep and ctx.has_msa_tree:
        options = ctx.post_msa_tree_options
        logger.info(f"# post_prep.msa_tree_mode: {options['mode']}")
        logger.info(f"# post_prep.msa_tree_min_taxa: {options['min_taxa']}")
        logger.info(f"# post_prep.msa_tree_max_samples: {options['max_samples']}")
        logger.info(f"# post_prep.msa_tree_model: {options['model']}")
        logger.info(
            f"# post_prep.msa_tree_bootstrap_replicates: {options['bootstrap_replicates']}"
        )


def _log_primer_inputs(ctx: _BuildContext) -> None:
    if not ctx.post_prep or not ctx.has_primer_trim:
        return
    logger = ctx.run_logger
    options = ctx.post_primer_trim_options
    logger.info(f"# post_prep.primer_file: {ctx.post_primer_file}")
    logger.info(f"# post_prep.primer_set: {', '.join(ctx.post_primer_set_names)}")
    logger.info(f"# post_prep.primer_forward_count: {len(ctx.post_primer_forward)}")
    logger.info(f"# post_prep.primer_reverse_count: {len(ctx.post_primer_reverse)}")
    logger.info(f"# post_prep.primer_trim_mode: {options['trim_mode']}")
    logger.info(f"# post_prep.primer_max_mismatch: {options['max_mismatch']}")
    logger.info(f"# post_prep.primer_max_error_rate: {options['max_error_rate']}")
    logger.info(f"# post_prep.primer_min_overlap_bp: {options['min_overlap_bp']}")
    logger.info(f"# post_prep.primer_min_overlap_ratio: {options['min_overlap_ratio']}")
    logger.info(f"# post_prep.primer_end_max_offset: {options['end_max_offset']}")
    logger.info(f"# post_prep.primer_iter_enable: {options['iter_enable']}")
    logger.info(f"# post_prep.primer_iter_max_rounds: {options['iter_max_rounds']}")
    logger.info(f"# post_prep.primer_sidecar_format: {options['sidecar_format']}")
    logger.info(f"# post_prep.primer_recheck_tool: {options['recheck_tool']}")
    logger.info(
        f"# post_prep.primer_recheck_min_identity: {options['recheck_min_identity']}"
    )
    logger.info(
        f"# post_prep.primer_recheck_min_query_cov: {options['recheck_min_query_cov']}"
    )


def _initialize_build_runtime(ctx: _BuildContext) -> None:
    ctx.progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        disable=not console.is_terminal,
    )
    _log_build_inputs(ctx)
    _log_primer_inputs(ctx)
    if ctx.warnings:
        ctx.run_logger.info("# warnings:")
        for warning in ctx.warnings:
            ctx.run_logger.info(f"# - {warning}")
    ctx.run_logger.info(f"# workers: {ctx.workers}")


def _process_ncbi_chunks(
    ctx: _BuildContext,
    taxid: str,
    count: int | None,
    data_iter: Iterable[tuple[int, str]],
    spool_f: Any,
    task_id: int,
) -> None:
    if ctx.workers < 1:
        raise typer.BadParameter("--workers must be >= 1.")
    queue: Queue = Queue(maxsize=max(1, ctx.workers * 2))
    stop_event = Event()
    errors: list[Exception] = []

    def worker() -> None:
        while True:
            item = queue.get()
            if item is None:
                queue.task_done()
                break
            try:
                _, chunk = item
                records = extract_ncbi_records_from_genbank_chunk(
                    chunk,
                    ctx.marker_rules,
                    ctx.acc_to_seqs,
                    ctx.counters,
                    ctx.dup_accessions,
                    ctx.lock,
                    ctx.progress,
                    task_id,
                    taxid,
                    ctx.dump_gb,
                )
                append_records_to_spool(records, spool_f, ctx.lock)
            except Exception as exc:
                errors.append(exc)
                stop_event.set()
            finally:
                queue.task_done()

    threads = [Thread(target=worker, daemon=True) for _ in range(ctx.workers)]
    for thread in threads:
        thread.start()
    for start, chunk in data_iter:
        if stop_event.is_set():
            break
        if not chunk:
            continue
        if count is not None and count > 0:
            per_query = int(ctx.ncbi_cfg.get("per_query", 100))
            fetched = min(start + per_query, count)
            ctx.run_logger.info(f"# fetch progress taxid={taxid}: {fetched}/{count}")
        queue.put((start, chunk))
    for _ in threads:
        queue.put(None)
    queue.join()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


def _run_ncbi_stage(ctx: _BuildContext, spool_f: Any) -> None:
    if not ctx.uses_ncbi:
        return
    for taxid in ctx.taxids:
        query = build_query(taxid, ctx.marker_query, ctx.filters_cfg, ctx.taxon_noexp)
        ctx.run_logger.info(f"# query taxid={taxid}: {query}")
        delay_sec = default_delay(ctx.ncbi_cfg)
        if ctx.from_gb:
            data_iter = iter_genbank_files(ctx.from_gb, taxid)
            count = None
            ctx.run_logger.info(f"# query count taxid={taxid}: from-gb")
        else:
            count, data_iter = fetch_genbank(
                query,
                ctx.ncbi_cfg,
                delay_sec,
                dump_dir=ctx.dump_gb,
                resume=ctx.resume,
                taxid=taxid,
            )
            ctx.run_logger.info(f"# query count taxid={taxid}: {count}")
            if count == 0:
                console.print(f"[yellow]taxid {taxid}: 0 records[/yellow]")
                continue
            ctx.run_logger.info(f"# fetch progress taxid={taxid}: 0/{count}")
        task_id = ctx.progress.add_task(f"taxid {taxid}", total=count)
        _process_ncbi_chunks(ctx, taxid, count, data_iter, spool_f, task_id)


def _run_bold_stage(
    ctx: _BuildContext,
    spool_f: Any,
    spool_dir: Path,
) -> None:
    if not ctx.uses_bold:
        return

    ncbi_accessions = set(ctx.acc_to_seqs.keys())

    for resolved_taxon in ctx.resolved_taxa:
        scientific_name = resolved_taxon.scientific_name

        try:
            ctx.run_logger.info(
                f"# bold progress: taxon={scientific_name} phase=preprocess"
            )

            prepared_query = prepare_bold_query(
                scientific_name,
                ctx.cfg.get("bold"),
            )

            specimen_count = prepared_query.specimen_count or 0

            ctx.run_logger.info(
                f"# bold progress: taxon={scientific_name} "
                f"phase=summary specimens={specimen_count}"
            )

            if specimen_count > 0:
                ctx.run_logger.info(
                    f"# bold progress: taxon={scientific_name} phase=query"
                )

            process_bold_taxon_to_spool(
                resolved_taxon,
                prepared_query,
                ctx.marker_keys,
                ctx.marker_map,
                ctx.output_cfg,
                ctx.source,
                ctx.progress,
                spool_f,
                ctx.lock,
                ctx.counters,
                ctx.source_merge_rows,
                ncbi_accessions,
                spool_dir,
                ctx.run_logger,
            )
        except BoldApiError as exc:
            raise typer.BadParameter(str(exc)) from exc


def _merge_source_records(
    ctx: _BuildContext, ncbi_spool_path: Path, bold_spool_path: Path
) -> None:
    ncbi_records = load_records_from_spool(ncbi_spool_path)
    bold_records = load_records_from_spool(bold_spool_path)
    ncbi_records.sort(key=canonical_record_sort_key)
    bold_records.sort(key=canonical_record_sort_key)
    with ctx.out_path.open("w", encoding="utf-8") as out_f:
        if ncbi_records:
            emit_records_to_fasta(
                ncbi_records,
                out_f,
                ctx.counters,
                ctx.emitted_records,
                ctx.lock,
                source_merge_rows=ctx.source_merge_rows,
            )
        if bold_records:
            emit_records_to_fasta(
                bold_records,
                out_f,
                ctx.counters,
                ctx.emitted_records,
                ctx.lock,
                source_merge_rows=ctx.source_merge_rows,
            )


def _run_source_stages(ctx: _BuildContext, spool_dir: Path) -> None:
    ncbi_spool_path = spool_dir / "ncbi_records.jsonl"
    bold_spool_path = spool_dir / "bold_records.jsonl"
    with (
        ncbi_spool_path.open("w", encoding="utf-8") as ncbi_spool_f,
        bold_spool_path.open("w", encoding="utf-8") as bold_spool_f,
        ctx.progress,
    ):
        _run_ncbi_stage(ctx, ncbi_spool_f)
        _run_bold_stage(ctx, bold_spool_f, spool_dir)
    _merge_source_records(ctx, ncbi_spool_path, bold_spool_path)


def _run_primer_post_prep(ctx: _BuildContext) -> None:
    if PostPrepStep.PRIMER_TRIM.value not in ctx.post_prep_steps_run:
        return
    stats = apply_post_prep_primer_trim(
        ctx.out_path,
        ctx.post_primer_forward,
        ctx.post_primer_reverse,
        options=ctx.post_primer_trim_options,
    )
    ctx.counters["kept_records"] = stats["after"]
    ctx.run_logger.info(
        "# post_prep primer trim:"
        f" before={stats['before']} after={stats['after']} removed={stats['removed']}"
        f" trimmed_both={stats['trimmed_both']} trimmed_left_only={stats['trimmed_left_only']}"
        f" trimmed_right_only={stats['trimmed_right_only']} untrimmed={stats['untrimmed']}"
        f" dropped_empty={stats['dropped_empty']} canonical_orientation={stats['canonical_orientation']}"
        f" reverse_orientation={stats['reverse_orientation']} confidence_high={stats['confidence_high']}"
        f" confidence_medium={stats['confidence_medium']} confidence_low={stats['confidence_low']}"
        f" rounds_run={stats['rounds_run']} best_round={stats['best_round']}"
        f" high_conf_rate={stats['high_conf_rate']:.4f}"
    )
    if stats.get("sidecar_path"):
        ctx.run_logger.info(f"# post_prep primer sidecar: {stats['sidecar_path']}")
    if stats.get("retained_path"):
        ctx.run_logger.info(
            f"# post_prep primer retained_fasta: {stats['retained_path']}"
        )
    ctx.run_logger.info(
        "# post_prep primer recheck:"
        f" tool={stats.get('recheck_tool', 'off')} attempted={stats.get('recheck_attempted', 0)}"
        f" rescued={stats.get('recheck_rescued', 0)} error={stats.get('recheck_error') or 'none'}"
    )


def _run_length_post_prep(ctx: _BuildContext) -> None:
    if PostPrepStep.LENGTH_FILTER.value not in ctx.post_prep_steps_run:
        return
    stats = apply_post_prep_length_filter(ctx.out_path, ctx.post_min, ctx.post_max)
    ctx.counters["kept_records"] = stats["after"]
    ctx.run_logger.info(
        "# post_prep length filter:"
        f" before={stats['before']} after={stats['after']} removed={stats['removed']}"
    )


def _run_duplicate_post_prep(ctx: _BuildContext) -> None:
    if PostPrepStep.DUPLICATE_REPORT.value not in ctx.post_prep_steps_run:
        ctx.run_logger.info("# post_prep duplicate_acc_report: skipped (step disabled)")
        return
    records_path, groups_path, stats, reason = write_duplicate_acc_reports_csv(
        ctx.out_path, ctx.selected_header_formats
    )
    if reason:
        ctx.run_logger.info(f"# post_prep duplicate_acc_report: skipped ({reason})")
        console.print(
            f"[yellow]post_prep:[/yellow] duplicate ACC report skipped ({reason})."
        )
        return
    ctx.run_logger.info(
        "# post_prep duplicate_acc_report:"
        f" total={stats['total_records']} parsed={stats['parsed_records']}"
        f" unparsed={stats['unparsed_records']} groups={stats['duplicate_groups']}"
        f" records={stats['duplicate_records']} cross_organism_groups={stats['cross_organism_groups']}"
    )
    if records_path:
        ctx.run_logger.info(f"# post_prep duplicate_acc_records_csv: {records_path}")
        console.print(f"post_prep duplicate ACC records CSV: {records_path}")
    if groups_path:
        ctx.run_logger.info(f"# post_prep duplicate_acc_groups_csv: {groups_path}")
        console.print(f"post_prep duplicate ACC groups CSV: {groups_path}")


def _run_msa_tree_post_prep(ctx: _BuildContext) -> None:
    if PostPrepStep.MSA_TREE.value not in ctx.post_prep_steps_run:
        return
    taxid_by_header = {
        row["header"]: row["taxid"]
        for row in ctx.source_merge_rows
        if row.get("header") and row.get("taxid")
    }
    stats = apply_post_prep_msa_tree(
        ctx.out_path,
        ctx.post_msa_tree_options,
        taxid_by_header=taxid_by_header,
    )
    ctx.msa_tree_stats = stats
    ctx.run_logger.info(
        "# post_prep msa_tree:"
        f" mode={stats.get('mode', ctx.post_msa_tree_options['mode'])}"
        f" status={stats['status']} taxa={stats['taxa_count']}"
        f" msa={stats.get('msa_path') or 'none'} tree={stats.get('tree_path') or 'none'}"
    )
    for output in stats.get("tree_outputs", []):
        ctx.run_logger.info(
            "# post_prep msa_tree output:"
            f" taxid={output['taxid']} status={output['status']} taxa={output['taxa_count']}"
            f" msa={output['msa_path'] or 'none'} tree={output['tree_path'] or 'none'}"
        )


def _run_post_prep(ctx: _BuildContext) -> None:
    if not ctx.post_prep:
        return
    before = ctx.counters["kept_records"]
    ctx.run_logger.info(f"# kept records before post_prep: {before}")
    _run_primer_post_prep(ctx)
    _run_length_post_prep(ctx)
    _run_duplicate_post_prep(ctx)
    _run_msa_tree_post_prep(ctx)


def _show_build_result(ctx: _BuildContext, source_merge_path: Path) -> None:
    acc_species_map_path, stats = write_acc_organism_mapping_csv(
        ctx.out_path, ctx.emitted_records
    )
    ctx.run_logger.info(
        "# acc_organism_map:"
        f" total={stats['total_records']} mapped={stats['mapped_records']}"
        f" unmapped={stats['unmapped_records']} unused_source_records={stats['unused_records']}"
        f" unique_accessions={stats['unique_accessions']} unique_organisms={stats['unique_organisms']}"
    )
    ctx.run_logger.info(f"# acc_organism_map_csv: {acc_species_map_path}")
    for label, key in (
        ("total records", "total_records"),
        ("matched records", "matched_records"),
        ("matched features", "matched_features"),
        ("kept records", "kept_records"),
        ("skipped duplicates (same accession+sequence)", "skipped_same"),
        ("kept duplicates (same accession, different sequence)", "duplicated_diff"),
    ):
        ctx.run_logger.info(f"# {label}: {ctx.counters[key]}")
    if ctx.dup_accessions:
        ctx.run_logger.info("# duplicate accessions with different sequences:")
        for acc, count in sorted(ctx.dup_accessions.items()):
            ctx.run_logger.info(f"# - {acc}: {count} sequences")
    ctx.run_logger.info(f"# output: {ctx.out_path}")
    ctx.run_logger.info(f"# finished: {datetime.now().isoformat()}")
    render_result_table(
        ctx.counters["total_records"],
        ctx.counters["matched_records"],
        ctx.counters["matched_features"],
        ctx.counters["kept_records"],
        ctx.counters["skipped_same"],
        ctx.counters["duplicated_diff"],
        ctx.out_path,
        ctx.log_path,
        ctx.msa_tree_stats,
    )
    if ctx.dup_accessions:
        console.print(
            "[yellow]WARNING:[/yellow] duplicate accessions with different sequences were kept. See log for details."
        )
    console.print(f"ACC-organism mapping CSV: {acc_species_map_path}")
    console.print(f"Source merge CSV: {source_merge_path}")
    if stats["unmapped_records"] > 0:
        console.print(
            "[yellow]WARNING:[/yellow] Some final FASTA records could not be mapped to source ACC/organism. "
            "See .log for details."
        )


def _run_build_pipeline(**context_fields: Any) -> None:
    ctx = _BuildContext(**context_fields)

    with tee_console_output(ctx.log_path) as log_stream:
        run_logger = setup_run_logger(log_stream)
        ctx.run_logger = run_logger

        try:
            if _show_build_plan(ctx):
                return

            _initialize_build_runtime(ctx)

            with tempfile.TemporaryDirectory(
                prefix="taxondbbuilder_spool_"
            ) as spool_dir_name:
                spool_dir = Path(spool_dir_name)
                _run_source_stages(ctx, spool_dir)

                source_merge_path = write_source_merge_csv(
                    ctx.out_path,
                    ctx.source_merge_rows,
                )

                ctx.run_logger.info(f"# source_merge_csv: {source_merge_path}")

                _run_post_prep(ctx)
                _show_build_result(ctx, source_merge_path)
        finally:
            close_run_logger(run_logger)


@app.command("list-markers")
def list_markers(
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to TOML config file."
    ),
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
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to TOML config file."
    ),
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

    table = Table(
        title=f"Primer Sets ({primer_path})", show_header=True, header_style="bold"
    )
    table.add_column("Primer Set")
    table.add_column("Forward")
    table.add_column("Reverse")
    for key in sorted(primer_sets.keys()):
        entry = primer_sets[key]
        table.add_row(key, str(len(entry["forward"])), str(len(entry["reverse"])))
    console.print(table)


def _resolve_build_configuration(config: Path, source: BuildSource) -> dict[str, Any]:
    cfg = load_config(config, source=source)
    ncbi_cfg = cfg.get("ncbi", {})
    uses_ncbi = source in {BuildSource.NCBI, BuildSource.BOTH}
    uses_bold = source in {BuildSource.BOLD, BuildSource.BOTH}
    if uses_ncbi or uses_bold:
        setup_entrez(
            ncbi_cfg if isinstance(ncbi_cfg, dict) else {}, warn_if_missing=uses_ncbi
        )
    return {
        "cfg": cfg,
        "ncbi_cfg": ncbi_cfg,
        "filters_cfg": cfg.get("filters", {}),
        "output_cfg": cfg.get("output", {}),
        "taxon_noexp": bool(cfg.get("taxon", {}).get("noexp", False)),
        "uses_ncbi": uses_ncbi,
        "uses_bold": uses_bold,
        "marker_map": normalize_marker_map(cfg.get("markers", {}), source=source),
    }


def _resolve_build_markers(
    marker: list[str],
    marker_map: dict[str, dict[str, Any]],
    output_cfg: dict[str, Any],
    uses_ncbi: bool,
    output_prefix: str,
) -> dict[str, Any]:
    marker_keys = [resolve_marker_key(value, marker_map) for value in marker]
    selected_header_formats, marker_rules = _build_marker_rules(
        marker_keys, marker_map, output_cfg, uses_ncbi
    )
    return {
        "marker_keys": marker_keys,
        "marker_query": build_marker_query(marker_keys, marker_map)
        if uses_ncbi
        else "",
        "output_prefix": output_prefix.strip(),
        "selected_header_formats": selected_header_formats,
        "marker_rules": marker_rules,
    }


def _normalize_post_prep_requests(
    post_prep_step: list[PostPrepStep] | None,
    post_prep_primer_set: list[str] | None,
) -> tuple[list[str], list[str]]:
    requested_steps = [step.value for step in (post_prep_step or [])]
    requested_sets: list[str] = []
    for value in post_prep_primer_set or []:
        name = value.strip()
        if not name:
            raise typer.BadParameter(
                "--post-prep-primer-set cannot include empty values."
            )
        if name not in requested_sets:
            requested_sets.append(name)
    return requested_steps, requested_sets


def _build_post_prep_trim_options(
    post_prep_cfg: dict[str, Any], enabled: bool
) -> dict[str, Any]:
    return {
        "trim_mode": post_prep_cfg.get(
            "primer_trim_mode", PRIMER_TRIM_MODE_ONE_OR_BOTH
        ),
        "max_mismatch": int(post_prep_cfg.get("primer_max_mismatch", 0)),
        "max_error_rate": float(post_prep_cfg.get("primer_max_error_rate", 0.0)),
        "min_overlap_bp": post_prep_cfg.get("primer_min_overlap_bp"),
        "min_overlap_ratio": float(post_prep_cfg.get("primer_min_overlap_ratio", 1.0)),
        "end_max_offset": int(post_prep_cfg.get("primer_end_max_offset", 0)),
        "keep_retained_fasta": bool(
            post_prep_cfg.get("primer_keep_retained_fasta", True)
        ),
        "iter_enable": bool(post_prep_cfg.get("primer_iter_enable", False)),
        "iter_max_rounds": int(
            post_prep_cfg.get("primer_iter_max_rounds", 3 if enabled else 1)
        ),
        "iter_stop_delta": float(post_prep_cfg.get("primer_iter_stop_delta", 0.002)),
        "iter_target_conf": float(post_prep_cfg.get("primer_iter_target_conf", 0.98)),
        "sidecar_format": post_prep_cfg.get("primer_sidecar_format", "tsv"),
        "recheck_tool": post_prep_cfg.get("primer_recheck_tool", "off"),
        "recheck_min_identity": float(
            post_prep_cfg.get("primer_recheck_min_identity", 0.85)
        ),
        "recheck_min_query_cov": float(
            post_prep_cfg.get("primer_recheck_min_query_cov", 0.7)
        ),
    }


def _build_post_prep_msa_tree_options(
    post_prep_cfg: dict[str, Any],
) -> dict[str, Any]:
    return {
        "min_taxa": int(post_prep_cfg.get("msa_tree_min_taxa", 3)),
        "max_samples": int(post_prep_cfg.get("msa_tree_max_samples", 500)),
        "model": post_prep_cfg.get("msa_tree_model", "GTR+G"),
        "mode": post_prep_cfg.get("msa_tree_mode", "combined"),
        "bootstrap_replicates": int(
            post_prep_cfg.get("msa_tree_bootstrap_replicates", 1000)
        ),
    }


def _resolve_post_prep_primers(
    post_prep_cfg: dict[str, Any],
    requested_sets: list[str],
    config: Path,
) -> tuple[list[str], list[str], str | None, list[str]]:
    forward = list(post_prep_cfg.get("_primer_forward") or [])
    reverse = list(post_prep_cfg.get("_primer_reverse") or [])
    primer_file = post_prep_cfg.get("_primer_file_resolved") or post_prep_cfg.get(
        "primer_file"
    )
    set_names = list(post_prep_cfg.get("_primer_set_names") or [])
    if not set_names:
        configured = post_prep_cfg.get("primer_set")
        if isinstance(configured, str) and configured.strip():
            set_names = [configured.strip()]
        elif isinstance(configured, list):
            set_names = [
                str(value).strip() for value in configured if str(value).strip()
            ]
    if requested_sets:
        primer_sets_data = dict(BUILTIN_PRIMER_SETS)
        if primer_file:
            primer_path = resolve_support_file_path(str(primer_file), config, "Primer file")
            primer_sets_data.update(load_primer_sets_from_file(primer_path))
            primer_file = str(primer_path)
        forward, reverse = combine_primer_set_sequences(primer_sets_data, requested_sets)
        set_names = requested_sets
    return forward, reverse, primer_file, set_names


def _resolve_post_prep_options(
    post_prep: bool,
    post_prep_step: list[PostPrepStep] | None,
    post_prep_primer_set: list[str] | None,
    post_prep_cfg: dict[str, Any],
    config: Path,
    source: BuildSource,
) -> dict[str, Any]:
    requested_steps, requested_sets = _normalize_post_prep_requests(
        post_prep_step, post_prep_primer_set
    )
    if not post_prep:
        if requested_steps or requested_sets:
            raise typer.BadParameter(
                "--post-prep-step/--post-prep-primer-set requires --post-prep."
            )
        return {
            "post_prep": False,
            "post_prep_steps_run": [],
            "has_length_filter": False,
            "has_primer_trim": False,
            "has_msa_tree": False,
            "post_min": None,
            "post_max": None,
            "post_primer_forward": [],
            "post_primer_reverse": [],
            "post_primer_file": None,
            "post_primer_set_names": [],
            "post_primer_trim_options": _build_post_prep_trim_options({}, False),
            "post_msa_tree_options": _build_post_prep_msa_tree_options({}),
        }

    post_min = post_prep_cfg.get("sequence_length_min")
    post_max = post_prep_cfg.get("sequence_length_max")
    has_length_filter = post_min is not None or post_max is not None
    forward, reverse, primer_file, set_names = _resolve_post_prep_primers(
        post_prep_cfg, requested_sets, config
    )
    has_primer_trim = bool(forward and reverse)
    has_msa_tree = bool(post_prep_cfg.get("msa_tree_enable"))
    if PostPrepStep.PRIMER_TRIM.value in requested_steps and not has_primer_trim:
        raise typer.BadParameter(
            "post-prep step 'primer_trim' requires post_prep.primer_set (post_prep.primer_file is only needed for a custom, non-built-in primer set)."
        )
    if PostPrepStep.LENGTH_FILTER.value in requested_steps and not has_length_filter:
        raise typer.BadParameter(
            "post-prep step 'length_filter' requires post_prep.sequence_length_min or sequence_length_max."
        )
    if PostPrepStep.MSA_TREE.value in requested_steps and not has_msa_tree:
        raise typer.BadParameter(
            "post-prep step 'msa_tree' requires post_prep.msa_tree_enable = true."
        )
    if requested_steps:
        steps_run = [step for step in POST_PREP_STEP_ORDER if step in requested_steps]
    else:
        steps_run = []
        if has_primer_trim:
            steps_run.append(PostPrepStep.PRIMER_TRIM.value)
        if has_length_filter:
            steps_run.append(PostPrepStep.LENGTH_FILTER.value)
        if source != BuildSource.BOTH:
            steps_run.append(PostPrepStep.DUPLICATE_REPORT.value)
        if has_msa_tree:
            steps_run.append(PostPrepStep.MSA_TREE.value)
    return {
        "post_prep": True,
        "post_prep_steps_run": steps_run,
        "has_length_filter": has_length_filter,
        "has_primer_trim": has_primer_trim,
        "has_msa_tree": has_msa_tree,
        "post_min": int(post_min) if post_min is not None else None,
        "post_max": int(post_max) if post_max is not None else None,
        "post_primer_forward": forward,
        "post_primer_reverse": reverse,
        "post_primer_file": primer_file,
        "post_primer_set_names": set_names,
        "post_primer_trim_options": _build_post_prep_trim_options(post_prep_cfg, True),
        "post_msa_tree_options": _build_post_prep_msa_tree_options(post_prep_cfg),
    }


def _resolve_build_targets(
    config: Path,
    source: BuildSource,
    taxon: list[str],
    uses_bold: bool,
    out: Path | None,
    marker_keys: list[str],
    output_prefix: str,
    dump_gb: Path | None,
    from_gb: Path | None,
    resume: bool,
) -> dict[str, Any]:
    resolved_taxa: list[ResolvedTaxon] = []
    warnings: list[str] = []
    for value in taxon:
        resolved = resolve_taxon(value, require_scientific_name=uses_bold)
        resolved_taxa.append(resolved)
        if resolved.warning:
            warnings.append(resolved.warning)
    taxids = [item.taxid for item in resolved_taxa]
    out_path = build_output_path(out, taxids, marker_keys, output_prefix=output_prefix)
    if source == BuildSource.BOLD and (dump_gb or from_gb or resume):
        raise typer.BadParameter(
            "--dump-gb, --from-gb, and --resume are not supported with --source bold."
        )
    if resume and not dump_gb and not from_gb:
        raise typer.BadParameter("--resume requires --dump-gb or --from-gb.")
    if from_gb and not from_gb.exists():
        raise typer.BadParameter(f"--from-gb not found: {from_gb}")
    if dump_gb:
        dump_gb.mkdir(parents=True, exist_ok=True)
    return {
        "resolved_taxa": resolved_taxa,
        "taxids": taxids,
        "warnings": warnings,
        "out_path": out_path,
        "log_path": out_path.with_suffix(out_path.suffix + ".log"),
    }


@app.command()
def build(
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to TOML config file."
    ),
    taxon: list[str] = typer.Option(
        ..., "--taxon", "-t", help="Taxon (taxid or scientific name)."
    ),
    marker: list[str] = typer.Option(
        ..., "--marker", "-m", help="Marker key or prefix."
    ),
    source: BuildSource = typer.Option(
        BuildSource.NCBI,
        "--source",
        help="Sequence source: ncbi, bold, or both.",
    ),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Output file or directory."
    ),
    dump_gb: Path | None = typer.Option(
        None,
        "--dump-gb",
        help="Directory to store GenBank chunks for caching.",
    ),
    from_gb: Path | None = typer.Option(
        None,
        "--from-gb",
        help="Directory of GenBank chunks to extract without downloading.",
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Resume using cached GenBank chunks."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print query and exit."),
    workers: int = typer.Option(
        2, "--workers", "-w", help="Number of extraction workers."
    ),
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
    post_prep_step: list[PostPrepStep] | None = typer.Option(
        None,
        "--post-prep-step",
        help=(
            "Post-prep step(s) to run. Repeat to select multiple. "
            "Choices: primer_trim, length_filter, duplicate_report, msa_tree."
        ),
    ),
    post_prep_primer_set: list[str] | None = typer.Option(
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
    settings = _resolve_build_configuration(config, source)
    marker_settings = _resolve_build_markers(
        marker,
        settings["marker_map"],
        settings["output_cfg"],
        settings["uses_ncbi"],
        output_prefix,
    )
    post_settings = _resolve_post_prep_options(
        post_prep,
        post_prep_step,
        post_prep_primer_set,
        settings["cfg"].get("post_prep") or {},
        config,
        source,
    )
    target_settings = _resolve_build_targets(
        config,
        source,
        taxon,
        settings["uses_bold"],
        out,
        marker_settings["marker_keys"],
        marker_settings["output_prefix"],
        dump_gb,
        from_gb,
        resume,
    )
    _run_build_pipeline(
        **settings,
        config=config,
        source=source,
        taxon=taxon,
        **marker_settings,
        **post_settings,
        **target_settings,
        dump_gb=dump_gb,
        from_gb=from_gb,
        resume=resume,
        dry_run=dry_run,
        workers=workers,
    )

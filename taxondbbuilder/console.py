"""Console rendering helpers."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import BuildSource

console = Console()


def print_header() -> None:
    console.print(
        Panel(
            "Generic NCBI GenBank fetch + feature extraction",
            title="TaxonDBBuilder",
            subtitle="DB FASTA builder",
            expand=False,
        )
    )


def render_run_table(
    config: Path,
    source: BuildSource,
    taxids: list[str],
    markers: list[str],
    out_path: Path,
    filters_cfg: dict,
    dump_gb: Path | None = None,
    from_gb: Path | None = None,
    resume: bool = False,
) -> None:
    table = Table(title="Run Summary", show_header=True, header_style="bold")
    table.add_column("Item")
    table.add_column("Value", overflow="fold")
    table.add_row("Config", str(config))
    table.add_row("Source", source.value)
    table.add_row("Taxids", ", ".join(taxids))
    table.add_row("Markers", ", ".join(markers))
    table.add_row("Output", str(out_path))
    if filters_cfg:
        table.add_row("Filters", ", ".join(sorted(filters_cfg.keys())))
    else:
        table.add_row("Filters", "none")
    if from_gb:
        table.add_row("From GB", str(from_gb))
    if dump_gb:
        table.add_row("Dump GB", str(dump_gb))
    if resume:
        table.add_row("Resume", "true")
    console.print(table)


def render_result_table(
    total_records: int,
    matched_records: int,
    matched_features: int,
    kept_records: int,
    skipped_same: int,
    duplicated_diff: int,
    out_path: Path,
    log_path: Path,
) -> None:
    table = Table(title="Result Summary", show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Total records", str(total_records))
    table.add_row("Matched records", str(matched_records))
    table.add_row("Matched features", str(matched_features))
    table.add_row("Kept records", str(kept_records))
    table.add_row("Skipped duplicates (same acc+seq)", str(skipped_same))
    table.add_row("Kept duplicates (same acc, diff seq)", str(duplicated_diff))
    table.add_row("Output", str(out_path))
    table.add_row("Log", str(log_path))
    console.print(table)


def format_byte_count(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KiB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MiB"
    return f"{num_bytes / (1024 * 1024 * 1024):.1f} GiB"


def build_bold_download_description(
    scientific_name: str,
    downloaded_bytes: int,
    content_length: int | None,
) -> str:
    if content_length:
        return (
            f"BOLD {scientific_name}: download "
            f"{format_byte_count(downloaded_bytes)}/{format_byte_count(content_length)}"
        )
    return f"BOLD {scientific_name}: download {format_byte_count(downloaded_bytes)}"

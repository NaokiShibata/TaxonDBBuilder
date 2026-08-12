"""Reproducibility manifest output."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_f:
        for chunk in iter(lambda: input_f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version() -> str:
    candidates = [Path(__file__).resolve().parent.parent / "VERSION"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "VERSION")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return "unknown"


def write_run_manifest(
    fasta_path: Path,
    config_path: Path,
    *,
    source: str,
    taxon_inputs: list[str],
    resolved_taxa: list[Any],
    markers: list[str],
    ncbi_queries: list[str],
    bold_queries: list[dict[str, Any]],
    output_paths: list[Path],
) -> Path:
    manifest_path = fasta_path.with_suffix(fasta_path.suffix + ".manifest.json")
    unique_paths = sorted(
        {path.resolve() for path in output_paths if path.is_file()}, key=str
    )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "taxondbbuilder_version": _version(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "source": source,
        "taxon_inputs": taxon_inputs,
        "resolved_taxa": [asdict(item) for item in resolved_taxa],
        "markers": markers,
        "queries": {"ncbi": ncbi_queries, "bold": bold_queries},
        "config": {
            "path": str(config_path.resolve()),
            "sha256": _sha256(config_path),
        },
        "outputs": [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in unique_paths
        ],
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path

"""Read-only analysis of cached BOLD download TSVs.

Answers, per cached file: which taxon it belongs to (via run-log byte sizes),
the marker_code tally, how many rows carry a sequence, and why
normalize_bold_row() drops rows. Never writes to the cache.

Usage: python tools/bold_cache_audit.py <cache-dir> [--markers <markers.toml>]
"""
from __future__ import annotations

import argparse
import csv
import sys
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taxondbbuilder.bold_api import (  # noqa: E402
    _first_scalar,
    _sanitize_sequence,
    normalize_bold_row,
    resolve_marker_from_record,
)

# Cache files observed in test/.taxondbbuilder-cache; labelled from run logs
# by matching `bytes=` in `# bold query:` lines.
TAXON_BY_BYTES = {
    137998: "Silurus asotus (117 rows)",
    3165: "Silurus biwaensis (2 rows)",
    5632: "Silurus lithophilus (4 rows)",
    700526: "2026-08 MiFish run (large)",
}


def classify(row: dict, marker_keys, marker_map) -> str:
    marker_text = _first_scalar(row, ["marker_code", "marker", "markercode"])
    if not marker_text:
        return "no_marker_text"
    if not resolve_marker_from_record(marker_text, marker_keys, marker_map):
        return f"marker_not_in_config[{marker_text}]"
    seq = _sanitize_sequence(
        _first_scalar(row, ["nucleotides", "nuc", "sequence", "nucleotide", "nucleotidesequence"])
    )
    if not seq:
        return f"no_sequence[{marker_text}]"
    if normalize_bold_row(row, marker_keys, marker_map) is None:
        return f"normalize_returned_none[{marker_text}]"
    return f"MATCH[{marker_text}]"


def report(path: Path, marker_keys, marker_map) -> None:
    size = path.stat().st_size
    print(f"\n=== {path.name}  ({size} B)  {TAXON_BY_BYTES.get(size, f'bytes={size}')} ===")
    if size == 0:
        print("  (empty file — download never wrote a row)")
        return
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    print(f"  rows={len(rows)} columns={len(rows[0]) if rows else 0}")
    markers = Counter(
        (_first_scalar(r, ["marker_code", "marker", "markercode"]) or "<empty>") for r in rows
    )
    print("  marker_code tally:")
    for value, n in markers.most_common():
        print(f"    {n:4d}  {value}")
    seq_col = next((c for c in ("nucleotides", "nuc", "sequence") if rows and c in rows[0]), None)
    with_seq = sum(1 for r in rows if _sanitize_sequence(r.get(seq_col or "")))
    print(f"  sequence column={seq_col!r} rows_with_sequence={with_seq}/{len(rows)}")
    reasons = Counter(classify(r, marker_keys, marker_map) for r in rows)
    print("  normalize_bold_row outcome:")
    for reason, n in reasons.most_common():
        print(f"    {n:4d}  {reason}")
    _show_examples(rows, seq_col, marker_keys, marker_map)


def _show_examples(rows, seq_col, marker_keys, marker_map) -> None:
    """One concrete record per drop reason, so a claim is never just a count."""
    seen = set()
    for r in rows:
        reason = classify(r, marker_keys, marker_map)
        if reason.startswith("MATCH") or reason in seen:
            continue
        seen.add(reason)
        seq = _sanitize_sequence(r.get(seq_col or ""))
        print(
            f"    example {reason}: processid={r.get('processid', '')!r} "
            f"species={r.get('species', '')!r} seq_len={len(seq)}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache_dir", type=Path)
    ap.add_argument("--markers", type=Path, required=True)
    args = ap.parse_args()

    marker_map = tomllib.load(args.markers.open("rb")).get("markers", {})
    marker_keys = list(marker_map)

    for path in sorted(args.cache_dir.glob("query-*.tsv")):
        report(path, marker_keys, marker_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())

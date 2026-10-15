"""Read-only analysis of cached BOLD download TSVs.

Answers, per cached file: which taxon it belongs to (via run-log byte sizes),
the marker_code tally, how many rows carry a sequence, and why
normalize_bold_row() drops rows. Never writes to the cache.

Usage: python tools/bold_cache_audit.py <cache-dir> [--markers <markers.toml>]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import types
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


# Independently assigned gene identities for every code in the 840-row cache.
EXPECTED_GENES = {
    "COI-5P": "coi", "COII": "coii", "COXIII": "coiii", "CYTB": "cytb",
    "atp6": "atp6", "ND1": "nd1", "ND2": "nd2", "ND3": "nd3",
    "ND4": "nd4", "ND4L": "nd4l", "ND5-0": "nd5", "ND6": "nd6",
}


def compare_cache(paths, marker_map, baseline_source):
    """Compare a saved local baseline module without modifying cache files."""
    baseline = types.ModuleType("taxondbbuilder._audit_baseline")
    baseline.__package__ = "taxondbbuilder"
    sys.modules[baseline.__name__] = baseline
    exec(compile(baseline_source.read_text(), str(baseline_source), "exec"), baseline.__dict__)
    before_state = {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths}
    matrix = Counter()
    recognized = {"before": Counter(), "after": Counter()}
    keys = list(marker_map)
    changed = Counter()
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                code = _first_scalar(row, ["marker_code", "marker", "markercode"]) or ""
                expected = EXPECTED_GENES[code]  # Fail on unreviewed codes.
                old = baseline.resolve_marker_from_record(code, keys, marker_map)
                new = resolve_marker_from_record(code, keys, marker_map)
                old_key, new_key = (old[0] if old else None), (new[0] if new else None)
                matrix[(code, expected, old_key, new_key)] += 1
                for label, key in (("before", old_key), ("after", new_key)):
                    if key:
                        recognized[label][key] += 1
                changed["corrected"] += old_key != expected and new_key == expected
                changed["newly_excluded"] += old_key is not None and new_key is None
                changed["previously_recognized_now_unknown"] += old_key is not None and new_key is None
                for label, resolver in (("before", baseline.resolve_marker_from_record), ("after", resolve_marker_from_record)):
                    candidates = sum(resolver(code, [key], marker_map) is not None for key in keys)
                    changed[f"multiple_candidates_{label}"] += candidates > 1
    after_state = {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths}
    assert before_state == after_state, "cache content/mtime changed"
    return {"matrix": [dict(code=c, expected=e, before=b, after=a, count=n) for (c,e,b,a),n in sorted(matrix.items())],
            "recognized_by_key": recognized, "changes": changed, "cache_unchanged": True,
            "files": before_state}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache_dir", type=Path)
    ap.add_argument("--markers", type=Path, required=True)
    ap.add_argument("--baseline-source", type=Path)
    args = ap.parse_args()

    marker_map = tomllib.load(args.markers.open("rb")).get("markers", {})
    marker_keys = list(marker_map)

    paths = sorted(args.cache_dir.glob("query-*.tsv"))
    if args.baseline_source:
        print(json.dumps(compare_cache(paths, marker_map, args.baseline_source), indent=2))
        return 0
    for path in paths:
        report(path, marker_keys, marker_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())

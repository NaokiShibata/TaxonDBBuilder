#!/usr/bin/env python3
"""Benchmark one apply_post_prep_primer_trim run: elapsed time + output hashes.

Run as: python tools/bench_primer_trim.py --fasta <file> --workdir <dir>
The implementation used (Python or the Rust port behind the rust-anything
enable block) depends on RUST_ANYTHING_UNIT_PRIMER_TRIM in the environment.
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from taxondbbuilder.postprep.primer_trim import apply_post_prep_primer_trim

# configs/primers.toml [primer_sets.mifish_unity], the production set.
FORWARD = ["GYYGGTAAAWCTCGTGCCAGC"]
REVERSE = ["CATAGKRGGGTRTCTAATCCYMGTTTG"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fasta", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument(
        "--options",
        default="{}",
        help="primer trim options as JSON (production default: {})",
    )
    args = parser.parse_args()

    work = args.workdir
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    target = work / args.fasta.name
    shutil.copyfile(args.fasta, target)

    start = time.perf_counter()
    stats = apply_post_prep_primer_trim(
        target, FORWARD, REVERSE, options=json.loads(args.options)
    )
    elapsed = time.perf_counter() - start

    sidecar = Path(stats["sidecar_path"]) if stats["sidecar_path"] else None
    summary = {
        key: (round(value, 6) if isinstance(value, float) else value)
        for key, value in stats.items()
    }
    print(json.dumps(
        {
            "elapsed_s": round(elapsed, 3),
            "before": stats["before"],
            "after": stats["after"],
            "fasta_sha256": sha256(target),
            "sidecar_sha256": sha256(sidecar) if sidecar else None,
            "summary": summary,
        },
        ensure_ascii=False,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

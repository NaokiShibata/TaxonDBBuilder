#!/usr/bin/env bash
# Benchmark one primer_trim implementation over the production workload.
# Usage: tools/bench_primer_trim.sh <label> <runs> -- <python...>
#   e.g. tools/bench_primer_trim.sh python 2 -- /path/to/.venv/bin/python
# Each run gets a fresh workdir copy (apply_post_prep_primer_trim mutates the
# FASTA in place). The driver JSON carries elapsed_s and output sha256 hashes.
set -euo pipefail

label="$1"; runs="$2"; shift 3
[ "${1:-}" = "--" ] && shift
python_bin=("$@")

root="$(cd "$(dirname "$0")/.." && pwd)"
fasta="${BENCH_FASTA:-$root/test/test2/Results/db/20260208/MiFish_multi_taxon__12s.fasta}"
options="${BENCH_OPTIONS:-}"
[ -n "$options" ] || options='{}'
out="${BENCH_OUT:-$root/tmp/bench/$label}"
mkdir -p "$out"

for i in $(seq 1 "$runs"); do
  "${python_bin[@]}" "$root/tools/bench_primer_trim.py" --fasta "$fasta" --workdir "$out/run$i" --options "$options" | tee "$out/run$i.json"
done

#!/usr/bin/env bash
# Benchmark one build_taxonomy implementation: timing + output hashes.
# Usage: tools/bench_build_taxonomy.sh <label> <runs> -- <command...>
#   e.g. tools/bench_build_taxonomy.sh python 2 -- python3 tools/build_taxonomy_db.py
set -euo pipefail

label="$1"; runs="$2"; shift 3
[ "${1:-}" = "--" ] && shift
cmd_prefix=("$@")

root="$(cd "$(dirname "$0")/.." && pwd)"
names="${NAMES_DMP:-$root/taxdump/names.dmp}"
out="${BENCH_OUT:-$root/tmp/bench/$label}"
mkdir -p "$out"

csv="$out/taxid_scientific_name.csv"
db="$out/taxonomy.db"

for i in $(seq 1 "$runs"); do
  rm -f "$csv" "$db"
  start=$(date +%s.%N)
  "${cmd_prefix[@]}" all --names-dmp "$names" --out-csv "$csv" --out-db "$db" >"$out/run$i.log"
  end=$(date +%s.%N)
  printf '%s run%d: %.2fs\n' "$label" "$i" "$(echo "$end - $start" | bc)"
done

rows=$(tail -n +2 "$csv" | wc -l)
csv_hash=$(sha256sum "$csv" | cut -d' ' -f1)
db_dump_hash=$(sqlite3 "$db" '.dump' | sha256sum | cut -d' ' -f1)
printf 'label=%s rows=%s csv_sha256=%s db_dump_sha256=%s\n' "$label" "$rows" "$csv_hash" "$db_dump_hash"

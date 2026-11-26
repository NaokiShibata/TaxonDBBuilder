#!/usr/bin/env python3
"""
Build taxonomy lookup artifacts for Tauri GUI from NCBI taxdump names.dmp.

Outputs:
  - taxid_scientific_name.csv
  - taxonomy.db (SQLite, table: taxonomy)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from taxondbbuilder.taxonomy_db import (  # noqa: E402
    build_from_names_dmp,
    build_sqlite_from_csv,
    extract_scientific_names_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build tax_id <-> scientific_name artifacts from NCBI taxdump names.dmp"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser(
        "extract-csv", help="Extract scientific names from names.dmp to CSV"
    )
    p_extract.add_argument(
        "--names-dmp", type=Path, required=True, help="Path to names.dmp"
    )
    p_extract.add_argument(
        "--out-csv", type=Path, required=True, help="Output CSV path"
    )

    p_sqlite = sub.add_parser("build-sqlite", help="Build SQLite taxonomy.db from CSV")
    p_sqlite.add_argument("--in-csv", type=Path, required=True, help="Input CSV path")
    p_sqlite.add_argument(
        "--out-db", type=Path, required=True, help="Output SQLite DB path"
    )

    p_all = sub.add_parser("all", help="Run extract-csv and build-sqlite in one step")
    p_all.add_argument(
        "--names-dmp", type=Path, required=True, help="Path to names.dmp"
    )
    p_all.add_argument("--out-csv", type=Path, required=True, help="Output CSV path")
    p_all.add_argument(
        "--out-db", type=Path, required=True, help="Output SQLite DB path"
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.command == "extract-csv":
        count = extract_scientific_names_csv(args.names_dmp, args.out_csv)
        print(f"extracted {count} rows -> {args.out_csv}")
        return 0

    if args.command == "build-sqlite":
        count = build_sqlite_from_csv(args.in_csv, args.out_db)
        print(f"inserted {count} rows -> {args.out_db}")
        return 0

    if args.command == "all":
        extracted, inserted = build_from_names_dmp(
            args.names_dmp, args.out_csv, args.out_db
        )
        print(f"extracted {extracted} rows -> {args.out_csv}")
        print(f"inserted {inserted} rows -> {args.out_db}")
        return 0

    raise RuntimeError(f"unknown command: {args.command}")


# >>> rust-anything: build-taxonomy-db-rs is on. `rust-anything disable ../rust/build-taxonomy-db-rs` removes this block.
def _rust_anything_build_taxonomy_db_rs():
    import importlib, os, sys
    mode = os.environ.get("RUST_ANYTHING_MODE")
    if mode == "old" or os.environ.get("RUST_ANYTHING_UNIT_BUILD_TAXONOMY_DB") == "original":
        return
    at = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../rust/build-taxonomy-db-rs/python"))
    sys.path.insert(0, at)
    # A wrapper cached from another checkout would shadow the path insert above.
    for stale in ("build_taxonomy_db_rs", "build_taxonomy_db_rs._native"):
        sys.modules.pop(stale, None)
    try:
        native = importlib.import_module("build_taxonomy_db_rs")
        names = ["extract_scientific_names_csv", "build_sqlite_from_csv", "build_from_names_dmp"]
        impls = {name: getattr(native, name) for name in names}
        if not all(callable(f) for f in impls.values()):
            raise ImportError("build_taxonomy_db_rs exports are not callable")
    except (ImportError, AttributeError):
        sys.path.remove(at)
        if mode == "new":
            raise
        return
    sys.path.remove(at)
    globals().update(impls)


_rust_anything_build_taxonomy_db_rs()
del _rust_anything_build_taxonomy_db_rs
# <<< rust-anything: build-taxonomy-db-rs
if __name__ == "__main__":
    raise SystemExit(main())

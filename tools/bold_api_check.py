#!/usr/bin/env python3
"""Staged BOLD API check: preprocessor -> summary -> query -> download -> parse.

Use this once BOLD has cleared the client. It makes at most four requests per
taxon (three inside prepare_bold_query, one download) and writes only under
--out, so it is safe to run against the live API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The package is not installed into the environment, so make the repo root
# importable when this file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taxondbbuilder.bold_api import (  # noqa: E402
    BoldApiError,
    download_documents_to_path,
    get_bold_runtime_config,
    iter_document_rows_from_path,
    prepare_bold_query,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxon", default="Tachysurus ichikawai")
    parser.add_argument("--format", choices=["tsv", "json"], default="tsv")
    parser.add_argument("--max-rows", type=int, default=50, help="parse cap")
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="keep it 0 while diagnosing, so one failure is one request",
    )
    parser.add_argument("--out", type=Path, default=Path("/tmp/bold-api-check"))
    args = parser.parse_args()

    cfg = get_bold_runtime_config(
        {"retries": args.retries, "download_format": args.format}
    )

    try:
        prepared = prepare_bold_query(args.taxon, cfg)
    except BoldApiError as exc:
        print(f"FAIL preprocess/summary/query: {exc}")
        print(f"  {exc.details}")
        return 1
    print(
        f"stages 1-3 ok: normalized={prepared.normalized_query!r}"
        f" specimens={prepared.specimen_count}"
        f" token_len={len(prepared.query_id or '')}"
    )
    if not prepared.query_id:
        print("no query_id (specimen_count == 0); download stage skipped")
        return 0

    dest = args.out / f"check.{args.format}"
    try:
        meta = download_documents_to_path(prepared.query_id, cfg, dest)
    except BoldApiError as exc:
        print(f"FAIL download: {exc}")
        print(f"  {exc.details}")
        return 1
    print(
        f"stage 4 ok: bytes={meta['downloaded_bytes']}"
        f" content_length={meta['content_length']} path={meta['path']}"
    )

    rows = 0
    markers: dict[str, int] = {}
    for row in iter_document_rows_from_path(dest, args.format):
        rows += 1
        key = str(row.get("marker_code") or row.get("marker") or "?")
        markers[key] = markers.get(key, 0) + 1
        if rows >= args.max_rows:
            break
    print(f"stage 5 ok: rows_read={rows} (capped at {args.max_rows}) markers={markers}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

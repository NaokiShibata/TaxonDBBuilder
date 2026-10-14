#!/usr/bin/env python3
"""Staged BOLD API check: preprocessor -> summary -> query -> download -> parse.

Use this once BOLD has cleared the client. It makes at most four requests per
taxon (three inside prepare_bold_query, one download) and writes only under
--out, so it is safe to run against the live API.

--max-rows caps parsing only; the HTTP transfer happens first and the download
endpoint has no record-limit parameter, so --max-specimens is what bounds the
transfer by refusing to download a query that is too large.
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


def _refuse_download(specimen_count: int | None, max_specimens: int, allow_unknown: bool) -> str | None:
    """Why this query must not be downloaded, or None to go ahead.

    The download endpoint takes no record limit, so a large query can only be
    refused outright; there is no way to ask BOLD for a subset.
    """
    if specimen_count is None:
        if allow_unknown:
            return None
        return (
            "BOLD reported no specimen count, so the transfer size is unknown "
            "and this tool will not start an unbounded download. Re-run with "
            "--allow-unknown-count if you accept that."
        )
    if specimen_count > max_specimens:
        return (
            f"specimens={specimen_count} exceeds --max-specimens={max_specimens}. "
            "The download endpoint has no record-limit parameter, so a subset "
            "cannot be fetched; narrow the taxon or raise --max-specimens "
            "deliberately."
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxon", default="Tachysurus ichikawai")
    parser.add_argument("--format", choices=["tsv", "json"], default="tsv")
    parser.add_argument(
        "--max-rows",
        type=int,
        default=50,
        help="parse cap (does not limit the download)",
    )
    parser.add_argument(
        "--max-specimens",
        type=int,
        default=2000,
        help="abort before the download if the query is larger than this",
    )
    parser.add_argument(
        "--allow-unknown-count",
        action="store_true",
        help="download even when BOLD reported no specimen count",
    )
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

    refusal = _refuse_download(
        prepared.specimen_count, args.max_specimens, args.allow_unknown_count
    )
    if refusal:
        print(f"abort: {refusal}")
        return 2

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

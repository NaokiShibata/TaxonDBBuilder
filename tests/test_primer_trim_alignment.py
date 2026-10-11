"""Positional alignment guards for primer sidecar rows.

Sidecar rows are paired with records by position, not header. These pin the
ValueError guards that turn a silent zip truncation or a bare IndexError into
an explicit alignment failure.
"""

from __future__ import annotations

import pytest

from taxondbbuilder.postprep.primer_trim import (
    _aligned_sequences,
    _collect_vsearch_candidates,
)


def _row(header: str, seq_len: int, right_trim_bp: int = 0) -> dict:
    # Mirrors _build_primer_row: trim_end + right_trim_bp == len(seq).
    return {
        "record_id": header,
        "trim_end": seq_len - right_trim_bp,
        "right_trim_bp": right_trim_bp,
    }


def test_aligned_sequences_rejects_row_count_mismatch():
    records = [("A", "ACGT"), ("B", "ACG")]
    with pytest.raises(ValueError) as excinfo:
        _aligned_sequences(records, [_row("A", 4), _row("B", 3), _row("C", 1)])
    assert "3" in str(excinfo.value) and "2" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        _aligned_sequences(records, [_row("A", 4)])
    assert "1" in str(excinfo.value) and "2" in str(excinfo.value)


def test_aligned_sequences_rejects_swapped_headers():
    records = [("A", "ACGT"), ("B", "ACG")]
    rows = [_row("B", 3), _row("A", 4)]
    with pytest.raises(ValueError) as excinfo:
        _aligned_sequences(records, rows)
    message = str(excinfo.value)
    assert "row 0" in message and "'B'" in message and "'A'" in message


def test_aligned_sequences_catches_same_header_length_swap():
    # Header check alone cannot see this; the trim_end+right_trim_bp length
    # identity does.
    records = [("A", "ACGT"), ("A", "ACG")]
    rows = [_row("A", 3), _row("A", 4)]
    with pytest.raises(ValueError) as excinfo:
        _aligned_sequences(records, rows)
    message = str(excinfo.value)
    assert "row 0" in message and "4" in message and "sequence length 4" in message


def test_aligned_sequences_returns_sequences_in_record_order():
    records = [("A", "ACGT"), ("B", "ACG")]
    rows = [_row("A", 4, 1), _row("B", 3)]
    assert _aligned_sequences(records, rows) == ["ACGT", "ACG"]


def test_summarize_rows_to_records_rejects_count_mismatch():
    import taxondbbuilder as builder

    rows = [_row("A", 4), _row("B", 3)]
    with pytest.raises(ValueError) as excinfo:
        builder.summarize_rows_to_records(rows, ["ACGT"], "one_or_both")
    assert "2" in str(excinfo.value) and "1" in str(excinfo.value)

    # Aligned input still produces records (no regression).
    seqs = ["ACGT", "ACG"]
    records, summary = builder.summarize_rows_to_records(rows, seqs, "one_or_both")
    assert records == [("A", "ACGT"), ("B", "ACG")]
    assert summary["before"] == 2 and summary["after"] == 2


def test_collect_vsearch_candidates_rejects_count_mismatch():
    # Without the guard this reaches seqs[idx] and raises a bare IndexError.
    rows = [dict(_row("A", 4), confidence="low")]
    with pytest.raises(ValueError) as excinfo:
        _collect_vsearch_candidates(rows, [])
    assert "1" in str(excinfo.value) and "0" in str(excinfo.value)

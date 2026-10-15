"""Regression coverage for the 3.3.8 correctness audit."""
import pytest

@pytest.mark.parametrize("side,start,end,expected", [("L", 4, 23, 23), ("R", 6, 25, 23)])
def test_vsearch_trim_uses_query_coordinates(tmp_path, side, start, end, expected):
    from taxondbbuilder.postprep.primer_trim import (
        _parse_vsearch_hits, _merge_vsearch_hits, summarize_rows_to_records,
    )

    path = tmp_path / "hits.tsv"
    subject = "CL_0" if side == "L" else "CR_0"
    path.write_text(f"0|{side}\t{subject}\t100\t20\t0\t0\t{start}\t{end}\t1\t20\t0\t40\n")
    rows = [dict(record_id="record", confidence="low", left_hit=0, right_hit=0)]
    hits = _parse_vsearch_hits(path, [(subject, "A" * 20)], .8, .8)
    _merge_vsearch_hits(rows, [(0, side, f"0|{side}", 28)], hits)
    prefix = "left" if side == "L" else "right"
    assert rows[0][f"{prefix}_trim_bp"] == expected
    sequence = "GGG" + "A" * 20 + "C" * 30 if side == "L" else "C" * 30 + "A" * 20 + "GGG"
    records, _ = summarize_rows_to_records(rows, [sequence], "one_or_both")
    assert records == [("record", "C" * 30)]

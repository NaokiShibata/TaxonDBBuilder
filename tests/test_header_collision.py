"""Header-collision regression tests.

Same-shape positional joins exist in exports._collect_final_records,
postprep.duplicates._collect_acc_organism_mapping and
postprep.quality._annotated_rows; only the exports join is exercised here.
"""
from __future__ import annotations

import io
from pathlib import Path
from threading import Lock

import pytest


def _emit_bold_records(rows: list[dict]) -> tuple[str, list[dict[str, str]]]:
    """BOLD rows -> (FASTA text, source_merge/emitted rows). No files written."""
    import taxondbbuilder as builder

    marker_map = builder.normalize_marker_map(
        {"coi": {"aliases": ["coi"], "phrases": ["COI"],
                 "bold": {"marker_codes": ["COI-5P", "COI"]},
                 "header_format": "acc_only"}},
        builder.BuildSource.BOTH,
    )
    records = [
        builder.build_bold_canonical_record(
            builder.normalize_bold_row(row, ["coi"], marker_map),
            marker_map,
            {"header_formats": HEADER_FORMATS},
        )
        for row in rows
    ]
    out = io.StringIO()
    emitted: list[dict[str, str]] = []
    builder.emit_records_to_fasta(records, out, {"kept_records": 0}, emitted, Lock())
    return out.getvalue(), emitted


def _qiime2_join(
    fasta_path: Path, emitted: list[dict[str, str]]
) -> tuple[list[str], dict | None]:
    """Run the exports join once; return (taxonomy TSV body lines, result).

    An explicit collision error is the other acceptable outcome once the join
    stops trusting header position; in that case the caller returns early.
    """
    import taxondbbuilder as builder

    try:
        result = builder.write_interoperability_exports(
            fasta_path, emitted, [builder.ExportFormat.QIIME2]
        )[0]
    except ValueError as exc:
        message = str(exc).lower()
        assert "header" in message or COLLIDING_HEADER.lower() in message, str(exc)
        return [], None
    assert result["paths"][0].name.endswith(".qiime2.sequences.fasta")
    return result["paths"][1].read_text(encoding="utf-8").splitlines()[1:], result


HEADER_FORMATS = {"acc_only": "bold|{acc_id}"}
COLLIDING_HEADER = "bold|BOLD_SAM001"

# Case 1/2 rows (same sampleid, no processid -> same header).
SEQ_SHORT = "ACGTACGT"                              # 8 bp, removed by min_len=20
SEQ_LONG = "TTTTGGGGCCCAAACCCCTTTTGGGGCCCAAACCCC"   # 36 bp, survives
ROW_ALPHA_SHORT = {
    "marker_code": "COI-5P",
    "nucleotides": SEQ_SHORT,
    "sampleid": "SAM001",
    "insdc_acs": "AB111",
    "species": "Alpha fish",
    "genus": "Alpha",
    "family": "Alphidae",
}
ROW_BETA_LONG = {
    "marker_code": "COI-5P",
    "nucleotides": SEQ_LONG,
    "sampleid": "SAM001",
    "insdc_acs": "AB222",
    "species": "Beta fish",
    "genus": "Beta",
    "family": "Betidae",
}

# Case 4 rows (both survive any filter; only order diverges).
SEQ_A = "ACGTACGTACGTACGTACGTACGT"                  # 24 bp
SEQ_B = "TTTTGGGGCCCCAAAATTTTGGGGCCCCAAAA"          # 32 bp
ROW_ALPHA_A = {
    "marker_code": "COI-5P",
    "nucleotides": SEQ_A,
    "sampleid": "SAM001",
    "insdc_acs": "AB111",
    "species": "Alpha fish",
    "genus": "Alpha",
    "family": "Alphidae",
}
ROW_BETA_B = {
    "marker_code": "COI-5P",
    "nucleotides": SEQ_B,
    "sampleid": "SAM001",
    "insdc_acs": "AB222",
    "species": "Beta fish",
    "genus": "Beta",
    "family": "Betidae",
}

# Case 5 control rows: distinct sampleid -> distinct headers.
ROW_ALPHA_SHORT_OTHER = dict(ROW_ALPHA_SHORT, sampleid="SAM002")  # bold|BOLD_SAM002

# Case 3 sequences (written straight to FASTA, no emit needed).
SEQ_P_A = "AAAAAAAACCCCCCCC"   # 16 bp
SEQ_P_B = "GGGGGGGGTTTTTTTT"   # 16 bp


def test_bold_rows_without_processid_share_one_header() -> None:
    """Characterizes the cause: two different BOLD records collide in one header."""
    fasta_text, emitted = _emit_bold_records([ROW_ALPHA_SHORT, ROW_BETA_LONG])
    assert fasta_text == (
        f">{COLLIDING_HEADER}\n{SEQ_SHORT}\n>{COLLIDING_HEADER}\n{SEQ_LONG}\n"
    )
    assert [row["header"] for row in emitted] == [COLLIDING_HEADER, COLLIDING_HEADER]
    assert [row["processid"] for row in emitted] == ["", ""]
    assert [row["source_record_id"] for row in emitted] == ["SAM001", "SAM001"]
    assert [row["accession"] for row in emitted] == ["AB111", "AB222"]
    assert [row["organism_name"] for row in emitted] == ["Alpha fish", "Beta fish"]
    assert [row["taxonomy_lineage"] for row in emitted] == [
        "Alphidae; Alpha; Alpha fish",
        "Betidae; Beta; Beta fish",
    ]


@pytest.mark.xfail(
    strict=True,
    reason="positional header join pairs the surviving record with the removed record's source row",
)
def test_length_filter_keeps_the_surviving_records_taxonomy(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    fasta_path = tmp_path / "collision.fasta"
    fasta_text, emitted = _emit_bold_records([ROW_ALPHA_SHORT, ROW_BETA_LONG])
    fasta_path.write_text(fasta_text, encoding="utf-8")

    stats = builder.apply_post_prep_length_filter(fasta_path, 20, None)
    assert stats == {"before": 2, "after": 1, "removed": 1}
    assert fasta_path.read_text(encoding="utf-8") == f">{COLLIDING_HEADER}\n{SEQ_LONG}\n"

    taxa, result = _qiime2_join(fasta_path, emitted)
    if result is None:
        return                                        # explicit error: acceptable
    # The counts look clean, so nothing warns about the mismatched taxonomy.
    assert (result["exported_records"], result["skipped_records"]) == (1, 0)
    assert len(taxa) == 1
    assert taxa[0] == "BOLD_SAM001\tBetidae; Beta; Beta fish"


@pytest.mark.xfail(
    strict=True,
    reason="seq_by_header is last-wins, so one sequence is emitted twice",
)
def test_vsearch_recheck_keeps_both_sequences_for_one_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import taxondbbuilder as builder
    from taxondbbuilder.postprep import primer_trim

    def fake_run_vsearch(vsearch_bin, q_fa, db_fa, out_path, min_identity):
        out_path.write_text("", encoding="utf-8")
        return None

    monkeypatch.setattr(primer_trim, "_run_vsearch_command", fake_run_vsearch)
    fasta_path = tmp_path / "recheck.fasta"
    fasta_path.write_text(
        f">{COLLIDING_HEADER}\n{SEQ_P_A}\n>{COLLIDING_HEADER}\n{SEQ_P_B}\n",
        encoding="utf-8",
    )
    stats = builder.apply_post_prep_primer_trim(
        fasta_path,
        ["ACGTACGTACGT"],
        ["TGCATGCATGCA"],
        options={"recheck_tool": "vsearch", "keep_retained_fasta": False},
    )
    assert stats["after"] == 2
    assert fasta_path.read_text(encoding="utf-8") == (
        f">{COLLIDING_HEADER}\n{SEQ_P_A}\n>{COLLIDING_HEADER}\n{SEQ_P_B}\n"
    )


@pytest.mark.xfail(
    strict=True,
    reason="positional header join is order-dependent",
)
def test_exports_pair_each_occurrence_with_its_own_source_row(tmp_path: Path) -> None:
    _, emitted = _emit_bold_records([ROW_ALPHA_A, ROW_BETA_B])  # rows in emit order
    fasta_path = tmp_path / "order.fasta"
    # A FASTA rewrite that reorders records models what any post-prep step can do.
    fasta_path.write_text(
        f">{COLLIDING_HEADER}\n{SEQ_B}\n>{COLLIDING_HEADER}\n{SEQ_A}\n", encoding="utf-8"
    )
    taxa, _ = _qiime2_join(fasta_path, emitted)
    if not taxa:
        return
    sequences = (tmp_path / "order.fasta.qiime2.sequences.fasta").read_text(
        encoding="utf-8"
    ).splitlines()
    assert [sequences[1], sequences[3]] == [SEQ_B, SEQ_A]
    assert taxa == [
        "BOLD_SAM001\tBetidae; Beta; Beta fish",
        "BOLD_SAM001__2\tAlphidae; Alpha; Alpha fish",
    ]


def test_control_distinct_headers_survive_filter_and_export(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    fasta_text, emitted = _emit_bold_records([ROW_ALPHA_SHORT_OTHER, ROW_BETA_LONG])
    assert [row["header"] for row in emitted] == ["bold|BOLD_SAM002", COLLIDING_HEADER]
    fasta_path = tmp_path / "control.fasta"
    fasta_path.write_text(fasta_text, encoding="utf-8")
    assert builder.apply_post_prep_length_filter(fasta_path, 20, None) == {
        "before": 2, "after": 1, "removed": 1
    }
    result = builder.write_interoperability_exports(
        fasta_path, emitted, [builder.ExportFormat.QIIME2]
    )[0]
    assert (result["exported_records"], result["skipped_records"]) == (1, 0)
    assert result["paths"][1].read_text(encoding="utf-8") == (
        "Feature ID\tTaxon\nBOLD_SAM001\tBetidae; Beta; Beta fish\n"
    )
    assert fasta_path.read_text(encoding="utf-8") == f">{COLLIDING_HEADER}\n{SEQ_LONG}\n"

from __future__ import annotations

from pathlib import Path

from .conftest import json_text, read_golden


def test_bold_row_normalization_and_canonical_record(golden_dir: Path):
    import taxondbbuilder as builder
    from taxondb_bold import normalize_bold_row, parse_accession_tokens

    marker_map = builder.normalize_marker_map(
        {"coi": {"aliases": ["coi"], "phrases": ["COI"], "bold": {"marker_codes": ["COI-5P", "COI"]}}},
        builder.BuildSource.BOTH,
    )
    raw_row = {
        "marker_code": "COI-5P", "nucleotides": "acgt- 12!", "processid": "PRC001", "sampleid": "SAM001",
        "insdc_acs": "AB123, AB124;AB123", "species": "Fish species",
    }
    normalized = normalize_bold_row(raw_row, ["coi"], marker_map)
    assert normalized is not None
    record = builder.build_bold_canonical_record(
        normalized, marker_map, {"header_formats": {"verbose": "{db}|{acc_id}|{organism_raw}|{marker_raw}"}},
    )
    assert parse_accession_tokens(normalized["accession"]) == ["AB123", "AB124"]
    assert json_text(builder.canonical_record_to_dict(record)) == read_golden(golden_dir, "bold-record.json")
    assert normalize_bold_row({"marker_code": "unknown", "nucleotides": "ACGT"}, ["coi"], marker_map) is None


def test_primer_pure_functions_are_characterized():
    import taxondbbuilder as builder

    assert builder.required_overlap_bp(10, None, 0.55) == 6
    assert builder.required_overlap_bp(10, 8, 0.55) == 8
    assert builder.count_mismatches("ACGT", "ANNT") == 0
    assert builder.count_mismatches("ACGT", "AAAA") == 3
    prefix = builder.find_best_prefix_match("TTACGTAA", ["ACGT"], 0, 0.0, None, 1.0, 2)
    suffix = builder.find_best_suffix_match("GGACGT", ["ACGT"], 0, 0.0, None, 1.0, 0)
    assert prefix is not None and suffix is not None
    assert (prefix.overlap_bp, prefix.trim_bp, prefix.full_len_match) == (4, 6, True)
    assert (suffix.overlap_bp, suffix.trim_bp, suffix.full_len_match) == (4, 4, True)
    canonical = builder.OrientationScore("canonical", prefix, suffix)
    reverse = builder.OrientationScore("reverse", None, suffix)
    assert builder.resolve_orientation("ignored", canonical, reverse) == (canonical, False)
    assert builder.resolve_orientation("ignored", canonical, canonical) == (canonical, True)
    assert [builder.confidence_label(*args) for args in [(2, 0, False), (2, 2, False), (1, 0, False), (0, 0, False), (2, 0, True)]] == ["high", "medium", "medium", "low", "medium"]
    row = {"left_hit": 1, "left_trim_bp": 5, "right_hit": 0, "right_overlap_bp": 4}
    assert builder.compute_trim_lengths_from_row(row, builder.PRIMER_TRIM_MODE_ONE_OR_BOTH) == (5, 0)
    assert builder.compute_trim_lengths_from_row(row, builder.PRIMER_TRIM_MODE_BOTH_REQUIRED) == (0, 0)


def test_primer_normalization_and_combination():
    import taxondbbuilder as builder

    data = {
        "alpha": {"forward": ["ACGT"], "reverse": ["TGCA"]},
        "beta": {"forward": ["RGTU"], "reverse": ["CARY"]},
    }
    assert builder.normalize_primer_values(["rgu"], "fixture", "forward") == ["RGT"]
    assert builder.combine_primer_set_sequences(data, ["alpha", "beta"]) == (["ACGT", "RGTU"], ["TGCA", "CARY"])

from __future__ import annotations

from pathlib import Path
import re

import pytest
import typer


def test_load_config_source_branches_and_normalization(fixture_dir: Path):
    import taxondbbuilder as builder

    config_path = fixture_dir / "minimal_config.toml"
    ncbi = builder.load_config(config_path, builder.BuildSource.NCBI)
    bold = builder.load_config(config_path, builder.BuildSource.BOLD)
    both = builder.load_config(config_path, builder.BuildSource.BOTH)

    assert sorted(ncbi["markers"]) == ["12s", "coi"]
    assert ncbi["post_prep"]["primer_max_mismatch"] == 0
    assert ncbi["post_prep"]["primer_trim_mode"] == builder.PRIMER_TRIM_MODE_ONE_OR_BOTH
    assert ncbi["post_prep"]["_primer_forward"] == ["ACGT"]
    assert ncbi["post_prep"]["_primer_reverse"] == ["TGCA"]
    assert bold["markers"]["coi"]["bold"]["marker_codes"] == ["COI-5P", "COI"]
    assert both["bold"] == {}


@pytest.mark.parametrize(
    ("source", "config", "message"),
    [
        ("ncbi", "[markers]\nfoo = {}\n", "Missing [ncbi] section in config."),
        ("both", "[markers]\nfoo = {}\n", "Missing [ncbi] section in config."),
        ("bold", "[ncbi]\nemail = 'x'\n", "Missing [markers] section in config."),
    ],
)
def test_load_config_validation_messages(tmp_path: Path, source: str, config: str, message: str):
    import taxondbbuilder as builder

    path = tmp_path / "bad.toml"
    path.write_text(config, encoding="utf-8")
    with pytest.raises(typer.BadParameter, match=re.escape(message)):
        builder.load_config(path, builder.BuildSource(source))


def test_load_config_rejects_invalid_post_prep_range(tmp_path: Path):
    import taxondbbuilder as builder

    path = tmp_path / "bad.toml"
    path.write_text(
        "[ncbi]\n"
        "[markers.x]\nphrases = ['x']\n"
        "[post_prep]\nsequence_length_min = 8\nsequence_length_max = 2\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.BadParameter, match="sequence_length_min must be <="):
        builder.load_config(path)


def test_marker_normalization_resolution_query_and_region_patterns():
    import taxondbbuilder as builder

    raw = {
        "12s": {
            "aliases": ["12", "rrns"],
            "phrases": ["12S", 'rRNA "small"'],
            "region_patterns": [],
            "feature_types": ["rRNA"],
            "feature_fields": ["gene"],
            "bold": {"marker_codes": ["12S"]},
        },
        "raw": {"terms": ["complete[prop]"], "phrases": []},
    }
    marker_map = builder.normalize_marker_map(raw)
    assert marker_map == {
        "12s": {
            "phrases": ["12S", 'rRNA "small"'],
            "terms": [],
            "aliases": ["12", "rrns"],
            "region_patterns": [],
            "header_format": None,
            "feature_types": ["rRNA"],
            "feature_fields": ["gene"],
            "bold": {"marker_codes": ["12S"]},
        },
        "raw": {
            "phrases": [],
            "terms": ["complete[prop]"],
            "aliases": [],
            "region_patterns": [],
            "header_format": None,
            "feature_types": None,
            "feature_fields": None,
            "bold": {"marker_codes": []},
        },
    }
    assert builder.resolve_marker_key("RRNS", marker_map) == "12s"
    assert builder.resolve_marker_key("raw", marker_map) == "raw"
    assert builder.build_marker_query(["12s", "raw"], marker_map) == '(("12S"[All Fields]) OR ("rRNA \\"small\\""[All Fields]) OR (complete[prop]))'
    assert builder.build_region_patterns(marker_map["12s"]) == ["12S", "rRNA\\ small"]
    assert builder.build_region_patterns({"terms": ["gene[prop]"], "phrases": ["A B"]}) == ["gene", "A\\ B"]

    with pytest.raises(typer.BadParameter, match="not found"):
        builder.resolve_marker_key("unknown", marker_map)


def test_header_characterization():
    import taxondbbuilder as builder

    assert builder.sanitize_header("  Homo sapiens / COI  ") == "Homo_sapiens___COI"
    assert builder.resolve_header_format({"header_format": "simple"}, {"header_formats": {"simple": "{acc_id}|{loc}"}}) == "{acc_id}|{loc}"
    assert builder.resolve_header_format({}, {}) == builder.DEFAULT_HEADER_FORMAT
    assert builder.build_header("{acc_id}|{missing}|{organism}", {"acc_id": "A_1", "organism": "fish"}) == "A_1||fish"

    extractors, has_acc, has_org = builder.compile_header_extractors(
        ["{acc_id}|{organism_raw}|{marker}", "{acc_id}|{organism}|{loc}"]
    )
    assert (has_acc, has_org, len(extractors)) == (True, True, 2)
    assert builder.extract_header_fields_from_header("A_1|Test fish|COI", extractors) == ("A_1", "Test fish")
    assert builder.extract_header_fields_from_header("A_2|Other fish|12-20", extractors) == ("A_2", "Other fish")
    assert builder.extract_header_fields_from_header("no-match", extractors) == (None, None)

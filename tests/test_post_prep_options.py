"""Config-compatibility contract for ``_resolve_post_prep_options``.

The function merges CLI options with the ``[post_prep]`` TOML table, decides
which steps are enabled, validates explicitly requested steps and assembles the
options dict consumed by the build pipeline. These tests pin the observable
contract (returned keys, values, types, list order, exception type and exact
message) so the body can be restructured without changing behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer

from taxondbbuilder.cli import _resolve_post_prep_options
from taxondbbuilder.models import BuildSource, POST_PREP_STEP_ORDER, PostPrepStep

CONFIG = Path("tests/fixtures/post_prep_config.toml")

DISABLED = {
    "post_prep": False,
    "post_prep_steps_run": [],
    "has_length_filter": False,
    "has_quality_filter": False,
    "has_primer_trim": False,
    "has_msa_tree": False,
    "post_min": None,
    "post_max": None,
    "post_quality_options": {
        "max_ambiguous_fraction": None,
        "reject_invalid_iupac": True,
        "duplicate_policy": "keep",
    },
    "post_primer_forward": [],
    "post_primer_reverse": [],
    "post_primer_file": None,
    "post_primer_set_names": [],
    "post_primer_trim_options": {
        "trim_mode": "one_or_both",
        "max_mismatch": 0,
        "max_error_rate": 0.0,
        "min_overlap_bp": None,
        "min_overlap_ratio": 1.0,
        "end_max_offset": 0,
        "keep_retained_fasta": True,
        "iter_enable": False,
        "iter_max_rounds": 1,
        "iter_stop_delta": 0.002,
        "iter_target_conf": 0.98,
        "sidecar_format": "tsv",
        "recheck_tool": "off",
        "recheck_min_identity": 0.85,
        "recheck_min_query_cov": 0.7,
    },
    "post_msa_tree_options": {
        "min_taxa": 3,
        "max_samples": 500,
        "model": "GTR+G",
        "mode": "combined",
        "bootstrap_replicates": 1000,
    },
}

# Enabled run with an empty [post_prep] table: only the defaults differ.
ENABLED_DEFAULT_TRIM = dict(DISABLED["post_primer_trim_options"], iter_max_rounds=3)


def resolve(
    post_prep: bool = True,
    steps: list[PostPrepStep] | None = None,
    sets: list[str] | None = None,
    cfg: dict | None = None,
    source: BuildSource = BuildSource.NCBI,
    config: Path = CONFIG,
) -> dict:
    return _resolve_post_prep_options(
        post_prep, steps, sets, cfg or {}, config, source
    )


def test_post_prep_disabled_returns_the_fixed_option_set():
    result = resolve(post_prep=False)
    assert result == DISABLED
    assert list(result) == list(DISABLED)


@pytest.mark.parametrize(
    ("steps", "sets"),
    [
        ([PostPrepStep.MSA_TREE], None),
        (None, ["alpha"]),
        ([], ["alpha"]),
    ],
)
def test_requests_without_post_prep_are_rejected(steps, sets):
    with pytest.raises(
        typer.BadParameter,
        match=re.escape(
            "--post-prep-step/--post-prep-primer-set requires --post-prep."
        ),
    ):
        resolve(post_prep=False, steps=steps, sets=sets)


def test_enabled_defaults_match_disabled_except_for_flags():
    result = resolve()
    assert result["post_prep"] is True
    assert result["post_primer_trim_options"] == ENABLED_DEFAULT_TRIM
    assert result["post_msa_tree_options"] == DISABLED["post_msa_tree_options"]
    assert result["post_quality_options"] == DISABLED["post_quality_options"]
    for key in ("post_min", "post_max", "post_primer_file"):
        assert result[key] is None
    for key in ("post_primer_forward", "post_primer_reverse", "post_primer_set_names"):
        assert result[key] == []


@pytest.mark.parametrize(
    ("source", "expected_steps"),
    [
        (BuildSource.NCBI, ["duplicate_report"]),
        (BuildSource.BOLD, ["duplicate_report"]),
        (BuildSource.BOTH, []),
    ],
)
def test_duplicate_report_is_auto_selected_except_for_both_source(
    source: BuildSource, expected_steps: list[str]
):
    assert resolve(source=source)["post_prep_steps_run"] == expected_steps


def test_auto_selection_order_and_flags_when_everything_is_configured():
    cfg = {
        "_primer_forward": ["ACGT"],
        "_primer_reverse": ["TGCA"],
        "_primer_file_resolved": "/x/primers.toml",
        "_primer_set_names": ["alpha"],
        "primer_set": ["alpha"],
        "sequence_length_min": 4,
        "sequence_length_max": 10,
        "quality_max_ambiguous_fraction": 0.1,
        "duplicate_sequence_policy": "keep",
        "msa_tree_enable": True,
    }
    result = resolve(cfg=cfg)
    assert result["post_prep_steps_run"] == list(POST_PREP_STEP_ORDER)
    assert (
        result["has_primer_trim"],
        result["has_length_filter"],
        result["has_quality_filter"],
        result["has_msa_tree"],
    ) == (True, True, True, True)
    assert (result["post_min"], result["post_max"]) == (4, 10)
    assert result["post_primer_file"] == "/x/primers.toml"
    assert result["post_primer_set_names"] == ["alpha"]
    # BOTH drops duplicate_report but keeps the relative order.
    assert resolve(cfg=cfg, source=BuildSource.BOTH)["post_prep_steps_run"] == [
        "primer_trim",
        "length_filter",
        "quality_filter",
        "msa_tree",
    ]


def test_explicit_steps_are_deduplicated_and_sorted_by_pipeline_order():
    cfg = {
        "_primer_forward": ["ACGT"],
        "_primer_reverse": ["TGCA"],
        "msa_tree_enable": True,
    }
    result = resolve(
        steps=[PostPrepStep.MSA_TREE, PostPrepStep.PRIMER_TRIM, PostPrepStep.MSA_TREE],
        cfg=cfg,
    )
    assert result["post_prep_steps_run"] == ["primer_trim", "msa_tree"]
    # Explicit selection never adds duplicate_report implicitly.
    assert "duplicate_report" not in result["post_prep_steps_run"]


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (
            PostPrepStep.PRIMER_TRIM,
            "post-prep step 'primer_trim' requires post_prep.primer_set "
            "(post_prep.primer_file is only needed for a custom, non-built-in primer set).",
        ),
        (
            PostPrepStep.LENGTH_FILTER,
            "post-prep step 'length_filter' requires post_prep.sequence_length_min "
            "or sequence_length_max.",
        ),
        (
            PostPrepStep.QUALITY_FILTER,
            "post-prep step 'quality_filter' requires a post_prep quality option.",
        ),
        (
            PostPrepStep.MSA_TREE,
            "post-prep step 'msa_tree' requires post_prep.msa_tree_enable = true.",
        ),
    ],
)
def test_requested_step_without_prerequisites(step: PostPrepStep, message: str):
    with pytest.raises(typer.BadParameter) as excinfo:
        resolve(steps=[step])
    assert str(excinfo.value) == message


def test_missing_prerequisite_report_follows_pipeline_order():
    with pytest.raises(typer.BadParameter, match="'primer_trim' requires"):
        resolve(steps=[PostPrepStep.LENGTH_FILTER, PostPrepStep.PRIMER_TRIM])


def test_primer_trim_needs_both_primer_ends():
    cfg = {"_primer_forward": ["ACGT"]}
    result = resolve(cfg=cfg)
    assert result["has_primer_trim"] is False
    assert result["post_primer_forward"] == ["ACGT"]
    assert "primer_trim" not in result["post_prep_steps_run"]
    with pytest.raises(typer.BadParameter, match="'primer_trim' requires"):
        resolve(steps=[PostPrepStep.PRIMER_TRIM], cfg=cfg)


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({}, False),
        ({"sequence_length_min": 4}, True),
        ({"sequence_length_max": 10}, True),
        ({"sequence_length_min": None, "sequence_length_max": None}, False),
    ],
)
def test_length_filter_condition(cfg: dict, expected: bool):
    assert resolve(cfg=cfg)["has_length_filter"] is expected


def test_length_bounds_are_cast_to_int():
    result = resolve(cfg={"sequence_length_min": "4", "sequence_length_max": 9.9})
    assert (result["post_min"], result["post_max"]) == (4, 9)
    assert all(isinstance(value, int) for value in (result["post_min"], result["post_max"]))


@pytest.mark.parametrize(
    ("cfg", "expected", "options"),
    [
        ({}, False, {"max_ambiguous_fraction": None, "reject_invalid_iupac": True,
                     "duplicate_policy": "keep"}),
        ({"quality_max_ambiguous_fraction": 0.25}, True,
         {"max_ambiguous_fraction": 0.25, "reject_invalid_iupac": True,
          "duplicate_policy": "keep"}),
        # Key presence alone enables the filter, whatever the value is.
        ({"quality_reject_invalid_iupac": True}, True,
         {"max_ambiguous_fraction": None, "reject_invalid_iupac": True,
          "duplicate_policy": "keep"}),
        ({"quality_reject_invalid_iupac": False}, True,
         {"max_ambiguous_fraction": None, "reject_invalid_iupac": False,
          "duplicate_policy": "keep"}),
        ({"duplicate_sequence_policy": "remove"}, True,
         {"max_ambiguous_fraction": None, "reject_invalid_iupac": True,
          "duplicate_policy": "remove"}),
    ],
)
def test_quality_filter_condition_and_options(
    cfg: dict, expected: bool, options: dict
):
    result = resolve(cfg=cfg)
    assert result["has_quality_filter"] is expected
    assert result["post_quality_options"] == options


def test_msa_tree_requires_truthy_enable():
    assert resolve(cfg={"msa_tree_enable": False})["has_msa_tree"] is False
    assert resolve(cfg={"msa_tree_enable": True})["has_msa_tree"] is True
    assert resolve(cfg={"msa_tree_enable": True})["post_prep_steps_run"][-1] == "msa_tree"


def test_msa_tree_options_are_read_from_config():
    result = resolve(
        cfg={
            "msa_tree_enable": True,
            "msa_tree_min_taxa": 5,
            "msa_tree_max_samples": 20,
            "msa_tree_model": "HKY+G",
            "msa_tree_mode": "per_taxid",
            "msa_tree_bootstrap_replicates": 10,
        }
    )
    assert result["post_msa_tree_options"] == {
        "min_taxa": 5,
        "max_samples": 20,
        "model": "HKY+G",
        "mode": "per_taxid",
        "bootstrap_replicates": 10,
    }


def test_trim_options_are_read_from_config_when_enabled():
    result = resolve(
        cfg={
            "primer_trim_mode": "both_required",
            "primer_max_mismatch": 2,
            "primer_iter_enable": True,
            "primer_min_overlap_bp": 8,
            "primer_sidecar_format": "csv",
        }
    )
    options = result["post_primer_trim_options"]
    assert options["trim_mode"] == "both_required"
    assert options["max_mismatch"] == 2
    assert options["iter_enable"] is True
    assert options["iter_max_rounds"] == 3
    assert options["min_overlap_bp"] == 8
    assert options["sidecar_format"] == "csv"


def test_primers_come_from_the_resolved_config_values():
    cfg = {
        "_primer_forward": ["ACGT"],
        "_primer_reverse": ["TGCA"],
        "_primer_file_resolved": "/x/primers.toml",
        "_primer_set_names": ["alpha", "beta"],
    }
    result = resolve(cfg=cfg)
    assert result["post_primer_forward"] == ["ACGT"]
    assert result["post_primer_reverse"] == ["TGCA"]
    assert result["post_primer_file"] == "/x/primers.toml"
    assert result["post_primer_set_names"] == ["alpha", "beta"]


def test_configured_primer_set_without_resolution_stays_a_name_list():
    assert resolve(cfg={"primer_set": " mifish_u "})["post_primer_set_names"] == [
        "mifish_u"
    ]
    result = resolve(cfg={"primer_set": ["mifish_u", "", " mideca "]})
    assert result["post_primer_set_names"] == ["mifish_u", "mideca"]
    assert result["post_primer_forward"] == []
    # A non-string, non-list primer_set is ignored.
    assert resolve(cfg={"primer_set": 5})["post_primer_set_names"] == []


def test_requested_primer_sets_override_config_and_enable_trim():
    result = resolve(sets=["mifish_u", "mifish_u"], cfg={"primer_set": "mideca"})
    assert result["post_primer_set_names"] == ["mifish_u"]
    assert result["post_primer_forward"] == ["GTCGGTAAAACTCGTGCCAGC"]
    assert result["post_primer_reverse"] == ["CATAGTGGGGTATCTAATCCCAGTTTG"]
    assert result["has_primer_trim"] is True
    assert result["post_prep_steps_run"][0] == "primer_trim"


def test_requested_primer_sets_read_a_custom_primer_file():
    cfg = {"primer_file": "minimal_primers.toml"}
    config = Path("tests/fixtures/post_prep_config.toml")
    result = resolve(sets=["alpha"], cfg=cfg, config=config)
    assert result["post_primer_file"] == "tests/fixtures/minimal_primers.toml"
    assert result["post_primer_forward"] == ["ACGT"]
    assert result["post_primer_reverse"] == ["TGCA"]


def test_unknown_requested_primer_set_is_rejected():
    with pytest.raises(typer.BadParameter, match="Primer set 'nope' was not found"):
        resolve(sets=["nope"])


def test_missing_primer_file_is_rejected():
    with pytest.raises(typer.BadParameter, match="Primer file not found"):
        resolve(sets=["alpha"], cfg={"primer_file": "missing.toml"})


def test_blank_requested_primer_set_is_rejected():
    with pytest.raises(
        typer.BadParameter,
        match=re.escape("--post-prep-primer-set cannot include empty values."),
    ):
        resolve(sets=["  "])


def test_returned_keys_are_stable_across_branches():
    disabled = resolve(post_prep=False)
    enabled = resolve(cfg={"primer_set": "mifish_u", "sequence_length_min": 1})
    assert list(disabled) == list(enabled)
    assert set(enabled["post_prep_steps_run"]) <= set(POST_PREP_STEP_ORDER)

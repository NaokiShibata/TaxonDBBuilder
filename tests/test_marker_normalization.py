"""Config-compatibility contract for ``normalize_marker_map``.

The function mixes type validation, config-condition validation and
normalization. These tests pin the observable contract (defaults, output shape,
exception type and exact message of every rejection) so the implementation can
be restructured without changing what existing ``db.toml`` files see.
"""

from __future__ import annotations

import re

import pytest
import typer

from taxondbbuilder.markers import normalize_marker_map
from taxondbbuilder.models import BuildSource

# List-valued marker keys, in the order the implementation validates them.
LIST_FIELDS = (
    "phrases",
    "terms",
    "region_patterns",
    "aliases",
    "feature_types",
    "feature_fields",
    "bold.marker_codes",
)


def set_field(field: str, value: object) -> dict:
    """Build a marker cfg that sets a (possibly nested) field to ``value``."""
    cfg: dict = {"phrases": ["COI"]}
    if field.startswith("bold."):
        cfg["bold"] = {field.split(".", 1)[1]: value}
    else:
        cfg[field] = value
    return cfg


def message_of(field: str, value: object, **kwargs) -> str:
    with pytest.raises(typer.BadParameter) as excinfo:
        normalize_marker_map({"coi": set_field(field, value)}, **kwargs)
    return str(excinfo.value)


def test_minimal_marker_gets_every_default():
    assert normalize_marker_map({"coi": {"phrases": ["COI"]}}) == {
        "coi": {
            "phrases": ["COI"],
            "terms": [],
            "aliases": [],
            "region_patterns": [],
            "header_format": None,
            "feature_types": None,
            "feature_fields": None,
            "full_record": False,
            "bold": {"marker_codes": []},
        }
    }


def test_unset_and_empty_values_are_distinct():
    """phrases/terms/... default to [], but feature_types/feature_fields stay None."""
    result = normalize_marker_map(
        {"x": {"phrases": [], "terms": ["complete[prop]"], "region_patterns": []}}
    )["x"]
    assert result["phrases"] == []
    assert result["region_patterns"] == []
    assert result["feature_types"] is None
    assert result["feature_fields"] is None


def test_header_format_and_full_record_pass_through_without_validation():
    result = normalize_marker_map(
        {"x": {"phrases": ["COI"], "header_format": 0, "full_record": True}}
    )["x"]
    assert result["header_format"] == 0
    assert result["full_record"] is True


def test_mitogenome_fallback_keeps_deduplicated_targets():
    result = normalize_marker_map(
        {
            "mitogenome": {
                "phrases": ["mitochondrial"],
                "terms": ["complete[prop]"],
                "aliases": ["mito"],
                "region_patterns": ["COI"],
                "header_format": "{acc_id}",
                "feature_types": ["CDS"],
                "feature_fields": ["gene"],
                "full_record": False,
                "bold": {"marker_codes": ["COI-5P"]},
                "fallback": "mitogenome",
                "fallback_targets": ["COI", "COI", "12S"],
            }
        }
    )["mitogenome"]
    assert result["fallback"] == "mitogenome"
    assert result["fallback_targets"] == ["COI", "12S"]
    assert result["bold"] == {"marker_codes": ["COI-5P"]}


def test_fallback_keys_are_absent_when_fallback_is_none():
    result = normalize_marker_map({"x": {"phrases": ["COI"]}})["x"]
    assert "fallback" not in result
    assert "fallback_targets" not in result
    # Absent fallback is the same as an explicit "none".
    assert normalize_marker_map({"x": {"phrases": ["COI"], "fallback": "none"}}) == {
        "x": result
    }


def test_fallback_targets_are_validated_even_with_fallback_none():
    with pytest.raises(typer.BadParameter, match="fallback_targets must list known"):
        normalize_marker_map({"x": {"phrases": ["COI"], "fallback_targets": ["nope"]}})


@pytest.mark.parametrize(
    "field",
    LIST_FIELDS,
)
def test_non_list_field_is_rejected(field: str):
    assert message_of(field, {"not": "a list"}) == (
        f"markers.coi.{field} must be a list of strings."
    )


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_non_string_item_is_rejected(field: str):
    assert message_of(field, ["COI", 1]) == (
        f"markers.coi.{field} must contain only strings."
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("full_record", "yes", "markers.coi.full_record must be a boolean."),
        ("full_record", 1, "markers.coi.full_record must be a boolean."),
        ("fallback", "other", "markers.coi.fallback must be none or mitogenome."),
        ("fallback", ["mitogenome"], "markers.coi.fallback must be none or mitogenome."),
        (
            "fallback_targets",
            "COI",
            "markers.coi.fallback_targets must list known mitochondrial regions.",
        ),
        (
            "fallback_targets",
            ["COI", 1],
            "markers.coi.fallback_targets must list known mitochondrial regions.",
        ),
        (
            "fallback_targets",
            ["COI", "COI-5P"],
            "markers.coi.fallback_targets must list known mitochondrial regions.",
        ),
        ("bold", "COI", "markers.coi.bold must be a table (dict)."),
    ],
)
def test_invalid_scalar_or_table_values(field: str, value: object, message: str):
    assert message_of(field, value) == message


def test_mitogenome_fallback_requires_targets():
    with pytest.raises(
        typer.BadParameter,
        match=re.escape(
            "markers.coi.fallback_targets is required for mitogenome fallback."
        ),
    ):
        normalize_marker_map({"coi": {"phrases": ["COI"], "fallback": "mitogenome"}})


def test_ncbi_source_requires_phrases_or_terms():
    with pytest.raises(
        typer.BadParameter,
        match=re.escape(
            "markers.coi must define phrases or terms when source uses NCBI."
        ),
    ):
        normalize_marker_map({"coi": {"aliases": ["c"]}}, source=BuildSource.NCBI)


def test_both_source_reports_the_ncbi_requirement_first():
    with pytest.raises(typer.BadParameter, match="when source uses NCBI"):
        normalize_marker_map({"coi": {"bold": {"marker_codes": ["COI-5P"]}}},
                             source=BuildSource.BOTH)


def test_bold_source_requires_a_usable_marker_key():
    with pytest.raises(
        typer.BadParameter,
        match="when source uses BOLD",
    ):
        normalize_marker_map({"  ": {"bold": {"marker_codes": ["COI-5P"]}}},
                             source=BuildSource.BOLD)
    # A non-blank key is enough for BOLD; phrases/terms stay optional.
    assert normalize_marker_map({"coi": {}}, source=BuildSource.BOLD)["coi"][
        "bold"
    ] == {"marker_codes": []}


def test_blank_key_is_accepted_for_ncbi_only_builds():
    assert "" in normalize_marker_map({"": {"phrases": ["COI"]}},
                                      source=BuildSource.NCBI)


def test_validation_order_is_stable_across_markers_and_fields():
    """The first rejection in field order wins, so messages stay predictable."""
    assert message_of("full_record", "yes") == (
        "markers.coi.full_record must be a boolean."
    )
    both_bad = {"phrases": ["COI"], "full_record": "yes", "fallback": "other"}
    with pytest.raises(typer.BadParameter, match="full_record must be a boolean"):
        normalize_marker_map({"coi": both_bad})
    with pytest.raises(typer.BadParameter, match="fallback must be none or mitogenome"):
        normalize_marker_map({"coi": {"phrases": 0, "fallback": "other"}})
    with pytest.raises(typer.BadParameter, match="bold must be a table"):
        normalize_marker_map({"coi": {"phrases": ["COI"], "bold": "x", "feature_types": 0}})
    # Markers are validated in config order; the first broken one is reported.
    with pytest.raises(typer.BadParameter, match=re.escape("markers.b.fallback")):
        normalize_marker_map(
            {"a": {"phrases": ["COI"]}, "b": {"phrases": ["COI"], "fallback": "other"}}
        )


def test_falsy_non_list_field_reports_the_type_error():
    """`phrases = 0` is a type error, not a missing-phrase error."""
    assert message_of("phrases", 0) == "markers.coi.phrases must be a list of strings."
    assert message_of("aliases", "") == "markers.coi.aliases must be a list of strings."


def test_marker_map_keeps_config_key_order():
    marker_map = normalize_marker_map(
        {"coi": {"phrases": ["COI"]}, "12s": {"phrases": ["12S"]}}
    )
    assert list(marker_map) == ["coi", "12s"]

"""Contract for NCBI filter term building and header extractor compilation.

Both turn configuration into strings that end up in an Entrez query or in a
back-reference regex, so the exact term text and the rejection messages are the
contract pinned here.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pytest
import typer

from taxondbbuilder.headers import compile_header_extractors
from taxondbbuilder.ncbi import build_filter_terms


def test_filter_terms_cover_every_configured_facet():
    filters = {
        "filter": ["organism", "srcdb_refseq"],
        "properties": "biomol_mrna",
        "sequence_length_min": 100,
        "sequence_length_max": 300,
        "publication_date_from": "2020-01-02",
        "publication_date_to": "2024-12-31",
        "modification_date_from": date(2021, 2, 3),
        "all_fields_include": ["fin", "fish"],
        "all_fields_exclude": "larva",
        "raw": '"txid999"[Organism]',
    }
    assert build_filter_terms(filters) == [
        "organism[filter]",
        "srcdb_refseq[filter]",
        "biomol_mrna[prop]",
        "100[SLEN] : 300[SLEN]",
        "2020-01-02[PDAT] : 2024-12-31[PDAT]",
        "2021/02/03[MDAT] : 3000/12/31[MDAT]",
        '("fin"[All Fields] OR "fish"[All Fields])',
        'NOT ("larva"[All Fields])',
        '"txid999"[Organism]',
    ]


def test_dates_are_formatted_from_temporal_values():
    filters = {"modification_date_to": datetime(2030, 6, 1, 12, 0)}
    assert build_filter_terms(filters) == ["1800/01/01[MDAT] : 2030/06/01[MDAT]"]


def test_only_one_date_bound_still_fills_the_other():
    assert build_filter_terms({"publication_date_to": "2024-12-31"}) == [
        "1800/01/01[PDAT] : 2024-12-31[PDAT]"
    ]


def test_length_bounds_default_the_missing_side():
    assert build_filter_terms({"sequence_length_min": 5}) == [
        "5[SLEN] : 1000000000[SLEN]"
    ]
    assert build_filter_terms({"sequence_length_max": "50"}) == [
        "0[SLEN] : 50[SLEN]"
    ]


@pytest.mark.parametrize("filters", [None, {}])
def test_no_filters_produce_no_terms(filters):
    assert build_filter_terms(filters) == []


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        (
            {"filter": 5},
            "filters.filter must be a string or list of strings.",
        ),
        (
            {"properties": [1]},
            "filters.properties must be a string or list of strings.",
        ),
        (
            {"all_fields_include": {"a": 1}},
            "filters.all_fields_include must be a string or list of strings.",
        ),
        (
            {"all_fields_exclude": [None]},
            "filters.all_fields_exclude must be a string or list of strings.",
        ),
        ({"raw": 3}, "filters.raw must be a string or list of strings."),
        (
            {"publication_date_from": 5},
            "filters.publication_date_from must be a string or date.",
        ),
        (
            {"modification_date_to": ["x"]},
            "filters.modification_date_to must be a string or date.",
        ),
        (
            {"sequence_length_min": "many"},
            "filters.sequence_length_min/max must be integers.",
        ),
        (
            {"sequence_length_max": None, "sequence_length_min": []},
            "filters.sequence_length_min/max must be integers.",
        ),
    ],
)
def test_invalid_filter_values(filters, message):
    with pytest.raises(typer.BadParameter) as excinfo:
        build_filter_terms(filters)
    assert str(excinfo.value) == message


def test_filters_section_must_be_a_table():
    with pytest.raises(typer.BadParameter, match=re.escape("[filters] must be a table (dict).")):
        build_filter_terms(["not", "a", "table"])


def test_extractors_read_back_acc_id_and_organism():
    extractors, has_acc, has_org = compile_header_extractors(
        ["{acc_id}|{organism_raw}|{marker}", "{acc_id}|{organism}|{loc}"]
    )
    assert (has_acc, has_org, len(extractors)) == (True, True, 2)
    assert extractors[0].captures_organism is True


def test_templates_without_acc_id_only_set_the_flags():
    extractors, has_acc, has_org = compile_header_extractors(["{organism}|{marker}"])
    assert extractors == []
    assert (has_acc, has_org) == (False, True)


def test_organism_raw_takes_precedence_over_organism():
    extractors, _, _ = compile_header_extractors(["{acc_id}|{organism_raw}|{organism}"])
    assert extractors[0].captures_organism is True
    assert extractors[0].pattern.match("A_1|Test fish|extra").group("organism_name") == "Test fish"


def test_repeated_fields_use_backreferences():
    extractors, _, _ = compile_header_extractors(["{acc_id}|{acc_id}|{organism}|{organism}"])
    pattern = extractors[0].pattern
    assert pattern.match("A_1|A_1|fish|fish").group("acc_id") == "A_1"
    assert pattern.match("A_1|B_2|fish|fish") is None


def test_untracked_fields_are_matched_without_capture():
    extractors, _, _ = compile_header_extractors(["{acc_id}|{marker}|{loc}"])
    match = extractors[0].pattern.match("A_1|COI|10..200")
    assert match is not None
    assert set(match.groupdict()) == {"acc_id"}

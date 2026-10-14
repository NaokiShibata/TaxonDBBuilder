"""How a scientific name becomes a BOLD query, and what the cache really holds.

Evidence base: the cached downloads under test/.taxondbbuilder-cache and the
run logs in test/20261012 and test/20261014. Two facts drive these tests:

1. The preprocessor rewrites a `tax:<name>` term into whatever it recognises.
   For Silurus asotus that is `tax:species:Silurus asotus`; for Silurus tomodai
   and Tachysurus nudiceps it is three `ids:` terms, which match nothing.
   BOLDconnectR (the CRAN client) treats an `ids:` term as an error; we accept it.
2. The cached downloads for the Silurid taxa hold COI-5P and mitogenome genes
   and no 12S at all, so a 12s run legitimately matches zero rows.
"""

from __future__ import annotations

from typing import Any

import pytest

from taxondbbuilder import bold_api
from taxondbbuilder.bold_api import normalize_bold_row, prepare_bold_query

TOMODAI_TERMS = [
    {"scope": "ids", "field": "processid", "value": "Silurus tomodai"},
    {"scope": "ids", "field": "sampleid", "value": "Silurus tomodai"},
    {"scope": "ids", "field": "insdcacs", "value": "Silurus tomodai"},
]
ASOTUS_TERMS = [{"scope": "tax", "field": "species", "value": "Silurus asotus"}]


def router(payloads: dict[str, Any]):
    """Stand in for _request_json, keyed by endpoint path."""

    def _request(url: str, runtime_cfg: dict, stage: str = "request") -> Any:
        for path, payload in payloads.items():
            if path in url:
                return payload
        raise AssertionError(f"unexpected url {url}")

    return _request


def test_recognised_taxon_becomes_a_tax_species_triplet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        bold_api,
        "_request_json",
        router(
            {
                "/query/preprocessor": {"successful_terms": ASOTUS_TERMS},
                "/summary": {"counts": {"specimens": 99}},
                "/query": {"query_id": "tok"},
            }
        ),
    )
    prepared = prepare_bold_query("Silurus asotus", {})
    assert prepared.raw_query == "tax:Silurus asotus"
    assert prepared.normalized_query == "tax:species:Silurus asotus"
    assert prepared.specimen_count == 99


def test_unrecognised_taxon_is_rewritten_to_id_terms_by_the_preprocessor():
    """What the preprocessor answers for a name BOLD does not resolve.

    This pins the upstream behaviour we observed, not our handling of it.
    """
    terms = bold_api._extract_successful_terms({"successful_terms": TOMODAI_TERMS})
    assert terms == [
        "ids:processid:Silurus tomodai",
        "ids:sampleid:Silurus tomodai",
        "ids:insdcacs:Silurus tomodai",
    ]


@pytest.mark.xfail(
    strict=True,
    reason="prepare_bold_query accepts ids: terms as the taxon query; "
    "BOLDconnectR rejects them and the result is a silent 0-specimen search",
)
def test_id_terms_must_not_be_accepted_as_a_taxon_query(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        bold_api,
        "_request_json",
        router(
            {
                "/query/preprocessor": {"successful_terms": TOMODAI_TERMS},
                "/summary": {"counts": {"specimens": 0}},
            }
        ),
    )
    with pytest.raises(bold_api.BoldApiError):
        prepare_bold_query("Silurus tomodai", {})


@pytest.mark.xfail(
    strict=True,
    reason="a query the preprocessor resolved to ids: terms is still submitted; "
    "BOLDconnectR stops instead of sending a taxon search that can only return nothing",
)
def test_id_terms_are_not_submitted_as_a_query(monkeypatch: pytest.MonkeyPatch):
    submitted: list[str] = []

    def _request(url: str, runtime_cfg: dict, stage: str = "request") -> Any:
        if "/query/preprocessor" in url:
            return {"successful_terms": TOMODAI_TERMS}
        if "/summary" in url:
            return {"counts": {"specimens": 0}}
        if "/query" in url:
            submitted.append(url)
            return {"query_id": "tok"}
        raise AssertionError(url)

    monkeypatch.setattr(bold_api, "_request_json", _request)
    prepared = prepare_bold_query("Silurus tomodai", {})
    # Whatever the count says, an ids:-only taxon query should never be sent.
    assert not prepared.normalized_query.startswith("ids:")


# --- marker resolution against the codes BOLD actually emits -----------------

MITOGENOME_MARKERS = {
    "12s": {"aliases": ["12", "12s", "rrns"], "phrases": ["12S", "rrnS"], "bold": {"marker_codes": ["12S"]}},
    "coi": {"aliases": ["coi", "co1"], "phrases": ["COI"], "bold": {"marker_codes": ["COI-5P"]}},
    "coiii": {"aliases": ["coiii", "cox3"], "phrases": ["COIII", "COX3"]},
    # The rest of the set the 2026-10-12 run actually used.
    "coii": {"aliases": ["coii", "cox2"], "phrases": ["COII", "COX2"]},
    "cytb": {"aliases": ["cytb"], "phrases": ["CYTB", "cytochrome b"]},
    "nd1": {"aliases": ["nd1"], "phrases": ["ND1"]},
    "atp6": {"aliases": ["atp6"], "phrases": ["ATP6"]},
}


def test_bold_emits_coxiii_but_the_config_only_offers_coiii_and_cox3():
    """Pins today's gap: BOLD's code COXIII resolves to nothing.

    3 of 117 Silurus asotus rows carry marker_code=COXIII with a 781 bp
    sequence and are dropped even in a mitogenome-wide run.
    """
    resolved = bold_api.resolve_marker_from_record(
        "COXIII", ["coiii"], MITOGENOME_MARKERS
    )
    assert resolved is None


@pytest.mark.xfail(
    strict=True,
    reason="BOLD's own marker code COXIII should resolve to the coiii marker",
)
def test_coxiii_should_resolve_to_coiii():
    assert bold_api.resolve_marker_from_record(
        "COXIII", ["coiii"], MITOGENOME_MARKERS
    ) == ("coiii", "COXIII")


# --- the matched=0 case is a data fact, not a parse bug -----------------------

def _row(marker_code: str, sequence: str = "ACGT" * 200) -> dict[str, str]:
    return {
        "processid": f"PROC{marker_code}-12",
        "species": "Silurus asotus",
        "marker_code": marker_code,
        "nuc": sequence,
    }


# The cached Silurus asotus download: 99 COI-5P plus one set of mitogenome genes.
CACHED_ASOTUS_MARKERS = [
    "COI-5P",
    "COII",
    "COXIII",
    "CYTB",
    "atp6",
    "ND1",
    "ND2",
    "ND3",
    "ND4",
    "ND4L",
    "ND5-0",
    "ND6",
]


def test_a_12s_run_over_the_cached_asotus_rows_matches_nothing():
    """Every cached row has a sequence; none of them is 12S.

    matched=0 for Silurus asotus is the correct answer for a 12s request, not a
    parsing failure: 117/117 rows carry a nuc value.
    """
    rows = [_row(code) for code in CACHED_ASOTUS_MARKERS]
    assert all(row["nuc"] for row in rows)
    for row in rows:
        assert normalize_bold_row(row, ["12s"], MITOGENOME_MARKERS) is None


def test_the_same_rows_do_match_a_wider_marker_set():
    """The rows are parseable — only the marker request does not fit them."""
    keys = ["12s", "coi", "coiii", "coii", "cytb", "nd1", "atp6"]
    matched = [
        normalize_bold_row(_row(code), keys, MITOGENOME_MARKERS)
        for code in CACHED_ASOTUS_MARKERS
    ]
    # The keys this fixture covers resolve; COXIII and the ND genes left
    # out of `keys` do not.
    assert sum(1 for m in matched if m) == 5
    assert matched[0]["marker_key"] == "coi"


def test_a_12s_row_would_match_if_bold_had_one():
    """The 12s path itself works, so its absence upstream is not our bug."""
    row = _row("12S")
    matched = normalize_bold_row(row, ["12s"], MITOGENOME_MARKERS)
    assert matched is not None
    assert matched["marker_key"] == "12s"

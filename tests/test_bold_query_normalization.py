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

from pathlib import Path
from types import SimpleNamespace
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


def test_id_terms_leave_the_taxon_unresolved_and_are_never_sent(
    monkeypatch: pytest.MonkeyPatch,
):
    """An ids:-only answer is 'not taxonomy', not 'zero specimens'.

    BOLDconnectR stops on an ids: term for the same reason: a name searched as
    an id matches nothing, and reporting that as specimens=0 blames the data.
    """
    seen: list[str] = []

    def _request(url: str, runtime_cfg: dict, stage: str = "request") -> Any:
        seen.append(url)
        if "/query/preprocessor" in url:
            return {"successful_terms": TOMODAI_TERMS}
        raise AssertionError(f"summary/query must not be reached: {url}")

    monkeypatch.setattr(bold_api, "_request_json", _request)
    prepared = prepare_bold_query("Silurus tomodai", {})

    assert prepared.resolved is False
    assert prepared.specimen_count is None  # never counted, so not 0
    assert prepared.query_id is None
    assert prepared.normalized_query == ""
    assert "ids:processid:Silurus tomodai" in prepared.unresolved_reason
    assert len(seen) == 1  # preprocessor only


def test_unresolved_is_recorded_in_the_manifest_and_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Skipping BOLD for one taxon must leave a trace, not a silent gap."""
    from taxondbbuilder import cli
    from taxondbbuilder.bold_api import PreparedBoldQuery

    logger = _Log()
    prepared = PreparedBoldQuery(
        scientific_name="Silurus tomodai",
        raw_query="tax:Silurus tomodai",
        normalized_query="",
        specimen_count=None,
        query_id=None,
        runtime_cfg={},
        download_format="tsv",
        resolved=False,
        unresolved_reason="ids:processid:Silurus tomodai",
    )
    ctx = SimpleNamespace(
        uses_bold=True,
        resolved_taxa=[SimpleNamespace(scientific_name="Silurus tomodai", taxid=1)],
        cfg={},
        bold_queries=[],
        acc_to_seqs={},
        run_logger=logger,
        marker_keys=[],
        marker_map={},
        output_cfg={},
        source=None,
        progress=None,
        lock=None,
        sink=None,
        source_merge_rows=[],
        dump_gb=None,
        resume=False,
    )
    monkeypatch.setattr(cli, "prepare_bold_query", lambda *_a, **_k: prepared)
    reached: list[str] = []
    monkeypatch.setattr(
        cli,
        "process_bold_taxon_to_spool",
        lambda resolved, prep, *rest, **_kw: reached.append(prep.scientific_name),
    )
    with tmp_path.joinpath("spool.tsv").open("w", encoding="utf-8") as spool_f:
        cli._run_bold_stage(ctx, spool_f, tmp_path)

    assert ctx.bold_queries[0]["resolved"] is False
    assert ctx.bold_queries[0]["specimen_count"] is None
    summary = [line for line in logger.lines if "phase=summary" in line]
    assert summary and "specimens=unknown taxon_unresolved" in summary[0]
    assert "specimens=0" not in summary[0]
    assert not any("phase=query" in line for line in logger.lines)
    # The taxon still reaches the spool stage, which logs the reason and stops.
    assert reached == ["Silurus tomodai"]


def test_the_spool_stage_logs_the_reason_and_writes_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    import io
    import threading

    from taxondbbuilder.bold import process_bold_taxon_to_spool
    from taxondbbuilder.bold_api import PreparedBoldQuery

    logger = _Log()
    prepared = PreparedBoldQuery(
        scientific_name="Silurus tomodai",
        raw_query="tax:Silurus tomodai",
        normalized_query="",
        specimen_count=None,
        query_id=None,
        runtime_cfg={},
        download_format="tsv",
        resolved=False,
        unresolved_reason="ids:processid:Silurus tomodai;ids:sampleid:Silurus tomodai",
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        "taxondbbuilder.bold._fetch_bold_documents",
        lambda *_a, **_k: fetched.append("fetched"),
    )

    class _Progress:
        def add_task(self, *_a, **_k):
            return 1

        def update(self, *_a, **_k):
            return None

        def remove_task(self, *_a):
            return None

    class _Sink:
        def bump(self, *_a):
            return None

    process_bold_taxon_to_spool(
        SimpleNamespace(scientific_name="Silurus tomodai", taxid=1),
        prepared,
        [],
        {},
        {},
        None,
        _Progress(),
        io.StringIO(),
        threading.Lock(),
        _Sink(),
        [],
        set(),
        tmp_path,
        logger,
        cache_dir=tmp_path,
        resume=False,
    )

    closing = [line for line in logger.lines if line.startswith("# bold query:")]
    assert closing and "taxon_unresolved" in closing[0]
    assert "reason=ids:processid:Silurus tomodai" in closing[0]
    assert "specimens=0" not in closing[0]
    assert fetched == []


def test_a_http_error_is_never_reported_as_an_unresolved_taxon(
    monkeypatch: pytest.MonkeyPatch,
):
    """A 403 is a communication failure; it must not become taxon_unresolved."""

    def _request(url: str, runtime_cfg: dict, stage: str = "request") -> Any:
        raise bold_api.BoldApiError(
            "BOLD HTTP error 403: Forbidden",
            details="stage=preprocess status=403 attempts=1",
        )

    monkeypatch.setattr(bold_api, "_request_json", _request)
    with pytest.raises(bold_api.BoldApiError) as caught:
        prepare_bold_query("Silurus tomodai", {})
    assert caught.value.details.startswith("stage=preprocess status=403")


def test_a_taxon_term_beside_an_id_term_still_resolves(
    monkeypatch: pytest.MonkeyPatch,
):
    """Only an ids:-only answer is unresolved; a real taxon term wins."""
    monkeypatch.setattr(
        bold_api,
        "_request_json",
        router(
            {
                "/query/preprocessor": {
                    "successful_terms": ASOTUS_TERMS + [TOMODAI_TERMS[0]]
                },
                "/summary": {"counts": {"specimens": 99}},
                "/query": {"query_id": "tok"},
            }
        ),
    )
    prepared = prepare_bold_query("Silurus asotus", {})
    assert prepared.resolved is True
    assert prepared.specimen_count == 99
    assert prepared.normalized_query == "tax:species:Silurus asotus"


def test_a_non_taxonomy_term_is_left_alone(monkeypatch: pytest.MonkeyPatch):
    """geo:/bin: terms are neither taxonomy nor ids; they must not trip the check."""
    monkeypatch.setattr(
        bold_api,
        "_request_json",
        router(
            {
                "/query/preprocessor": {
                    "successful_terms": [
                        {"scope": "tax", "field": "species", "value": "Silurus asotus"},
                        {"scope": "geo", "field": "country/ocean", "value": "Japan"},
                    ]
                },
                "/summary": {"counts": {"specimens": 3}},
                "/query": {"query_id": "tok"},
            }
        ),
    )
    prepared = prepare_bold_query("Silurus asotus", {})
    assert prepared.resolved is True
    assert prepared.normalized_query == "tax:species:Silurus asotus;geo:country/ocean:Japan"


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


# --- marker resolution against the codes BOLD actually emits -----------------

ROOT = Path(__file__).resolve().parents[1]


def shipped_markers() -> dict[str, dict[str, Any]]:
    """The mitogenome marker set the product actually ships with."""
    import tomllib

    with (ROOT / "configs" / "markers_mitogenome.toml").open("rb") as fh:
        return tomllib.load(fh)["markers"]


SHIPPED = shipped_markers()

# The cached Silurus asotus download: 99 COI-5P plus one set of mitogenome
# genes, every row carrying a sequence. COXIII appears 3 times.
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


def _row(marker_code: str, processid: str = "", sequence: str = "ACGT" * 200) -> dict[str, str]:
    return {
        "processid": processid or f"PROC{marker_code}-12",
        "species": "Silurus asotus",
        "marker_code": marker_code,
        "nuc": sequence,
    }


def test_bold_coxiii_now_resolves_to_coiii():
    assert bold_api.resolve_marker_from_record("COXIII", ["coiii"], SHIPPED) == (
        "coiii",
        "COXIII",
    )


def test_the_shipped_config_declares_coxiii_as_a_bold_marker_code():
    assert SHIPPED["coiii"]["bold"]["marker_codes"] == ["COXIII"]


def test_coiii_still_matches_the_older_coiii_and_cox3_spellings():
    """Backward compatibility: the aliases and phrases still work."""
    for text in ("COIII", "COX3", "coiii", "cox3", "cytochrome c oxidase subunit III"):
        assert bold_api.resolve_marker_from_record(text, ["coiii"], SHIPPED)[0] == "coiii"


def test_coxiii_does_not_add_a_new_way_to_hit_coiii():
    """The added code matches COXIII exactly and nothing else."""
    assert bold_api.resolve_marker_from_record("COXIII", ["coiii"], SHIPPED)[0] == "coiii"
    # COXIII is not a substring of any coii candidate, so coii is unaffected.
    assert bold_api.resolve_marker_from_record("COXIII", ["coii"], SHIPPED) is None


@pytest.mark.parametrize("text,keys,expected", [
    ("COI-5P", ["coi", "coii"], "coi"), ("COI", ["coi", "coii"], "coi"),
    ("COII", ["coi", "coii"], "coii"), ("COII", ["coi"], None),
    ("COX2", ["coi", "coii"], "coii"), ("COIII", ["coi", "coii", "coiii"], "coiii"),
    ("COXIII", ["coi", "coii", "coiii"], "coiii"), ("COXIII", ["12s"], None),
    ("ND4", ["nd4", "nd4l"], "nd4"), ("ND4L", ["nd4", "nd4l"], "nd4l"),
    ("ND4L", ["nd4"], None),
    ("COI-5P-like", ["coi", "coii"], "coi"), ("16S ribosome", ["16s"], "16s"),
    ("cytochrome c oxidase subunit III", ["coi", "coii", "coiii"], "coiii"),
])
def test_marker_identity_is_independent_of_declaration_order(text, keys, expected):
    for ordered in (keys, keys[::-1]):
        result = bold_api.resolve_marker_from_record(text, ordered, dict(reversed(list(SHIPPED.items()))))
        assert (result[0] if result else None) == expected


def test_shared_alias_is_ambiguous():
    markers = {"a": {"aliases": ["shared"]}, "b": {"aliases": ["shared"]}}
    assert bold_api.resolve_marker_from_record("shared", list(markers), markers) is None


def test_a_12s_run_still_excludes_coxiii():
    assert normalize_bold_row(_row("COXIII"), ["12s"], SHIPPED) is None


def test_the_cached_asotus_rows_now_all_match_a_mitogenome_run():
    """The 3 COXIII rows that used to be dropped are identified again.

    Real example from the cache: processid=CYTC5134-12, 781 bp.
    """
    keys = list(SHIPPED)
    rows = [
        _row("COXIII", "CYTC5134-12", "ACGT" * 195),  # 781 bp, as cached
        _row("COXIII", "CYTC5135-12"),
        _row("COXIII", "CYTC5136-12"),
        _row("COI-5P"),
    ]
    matched = [normalize_bold_row(r, keys, SHIPPED) for r in rows]
    assert all(m is not None for m in matched)
    assert [m["marker_key"] for m in matched] == ["coiii", "coiii", "coiii", "coi"]
    assert matched[0]["sequence"].startswith("ACGT")


def test_a_full_cached_download_matches_every_row_now():
    """Before 3.3.6 one code in twelve was dropped; none is now."""
    keys = list(SHIPPED)
    matched = [normalize_bold_row(_row(code), keys, SHIPPED) for code in CACHED_ASOTUS_MARKERS]
    assert all(m is not None for m in matched)
    assert [m["marker_key"] for m in matched[:3]] == ["coi", "coii", "coiii"]


# --- the matched=0 case is a data fact, not a parse bug -----------------------


def test_a_12s_run_over_the_cached_asotus_rows_matches_nothing():
    """Every cached row has a sequence; none of them is 12S.

    matched=0 for Silurus asotus is the correct answer for a 12s request, not a
    parsing failure: 117/117 rows carry a nuc value.
    """
    rows = [_row(code) for code in CACHED_ASOTUS_MARKERS]
    assert all(row["nuc"] for row in rows)
    for row in rows:
        assert normalize_bold_row(row, ["12s"], SHIPPED) is None


def test_a_12s_row_would_match_if_bold_had_one():
    """The 12s path itself works, so its absence upstream is not our bug."""
    matched = normalize_bold_row(_row("12S"), ["12s"], SHIPPED)
    assert matched is not None
    assert matched["marker_key"] == "12s"

@pytest.mark.parametrize("name,terms,resolved", [
    ("Silurus asotus", ASOTUS_TERMS, True),
    ("  silurus   ASOTUS ", ASOTUS_TERMS, True),
    ("Silurus asotus", [{"scope": "tax", "field": "genus", "value": "Silurus"}], False),
    ("Silurus", [{"scope": "tax", "field": "genus", "value": "Silurus"}], True),
    ("Silurus asotus", [{"scope": "tax", "field": "species", "value": "Silurus biwaensis"}], False),
    ("Silurus asotus", TOMODAI_TERMS, False),
    ("Silurus asotus", [{"scope": "geo", "field": "country", "value": "Japan"}], False),
    ("Silurus asotus", [], False), ("Silurus asotus", ["unparseable"], False),
])
def test_query_scope_must_confirm_the_requested_name(monkeypatch, name, terms, resolved):
    calls = []
    def request(url, *_a, **_kw):
        calls.append(url)
        if "/preprocessor" in url:
            return {"successful_terms": terms}
        assert resolved, "unsafe scope reached summary/query"
        return {"counts": {"specimens": 0}}
    monkeypatch.setattr(bold_api, "_request_json", request)
    prepared = prepare_bold_query(name, {})
    assert prepared.resolved is resolved
    if not resolved:
        assert len(calls) == 1
        assert prepared.unresolved_reason
        assert prepared.query_id is None

@pytest.mark.parametrize("payload", [None, {}, [], {"successful_terms": None}])
def test_missing_preprocessor_taxonomy_is_unresolved(monkeypatch, payload):
    def request(url, *_a, **_kw):
        assert "/preprocessor" in url
        return payload
    monkeypatch.setattr(bold_api, "_request_json", request)
    assert not prepare_bold_query("Silurus asotus", {}).resolved


def test_renamed_taxon_is_ambiguous_without_synonym_evidence(monkeypatch):
    monkeypatch.setattr(bold_api, "_request_json", router({
        "/query/preprocessor": {"successful_terms": ["tax:species:Silurus synonymus"]}
    }))
    result = prepare_bold_query("Silurus asotus", {})
    assert not result.resolved
    assert result.unresolved_reason == "ambiguous_taxon_scope:tax:species:Silurus synonymus"


@pytest.mark.parametrize("text", ["ND14", "XND1", "ND1L", "ND1 / ND2"])
def test_marker_tokens_do_not_guess_a_gene(text):
    assert bold_api.resolve_marker_from_record(text, ["nd1", "nd2"], SHIPPED) is None

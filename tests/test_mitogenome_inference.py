"""Contract for mitochondrial region identification and repair failures.

``identify_region`` collects candidate identities from three evidence sources in
a fixed precedence, and ``resolve_regions`` reports every rejected repair as a
stable ``reason`` string. These tests pin both before the inference code is
restructured.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from Bio.Seq import Seq
from Bio.SeqFeature import SimpleLocation

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from test_mitogenome import feature, synthetic  # noqa: E402

from taxondbbuilder.mitogenome import (  # noqa: E402
    identify_region,
    ORDER,
    resolve_regions,
)

def test_flank_metadata_is_reported_with_the_inferred_region():
    rec, _ = synthetic("ND4")
    results, _ = resolve_regions(rec, ["ND4"])
    provenance = results[0][2]
    assert provenance["left_flank_id"] == ORDER[ORDER.index("ND4") - 1]
    assert provenance["right_flank_id"] == ORDER[ORDER.index("ND4") + 1]
    assert provenance["left_flank_location"] == "51..80"
    assert provenance["right_flank_location"] == "111..140"
    assert provenance["transl_table"] == "2"
    assert provenance["extraction_method"] == "constrained_cds"
    assert provenance["boundary_evidence"] == "known_start_and_unique_stop"


@pytest.mark.parametrize(
    ("qualifiers", "expected"),
    [
        ({"product": ["tRNA-Leu"], "note": ["Anticodon: UAA"]}, "tRNA-Leu(UUR)"),
        ({"product": ["tRNA-Leu"], "note": ["SEQ:TAG"]}, "tRNA-Leu(CUN)"),
        ({"product": ["tRNA-Ser"], "note": ["Anticodon: TGA"]}, "tRNA-Ser(UCN)"),
        ({"product": ["tRNA-Ser"], "note": ["Anticodon: GCT"]}, "tRNA-Ser(AGY)"),
        # The anticodon qualifier alone carries no ANTICODON:/SEQ: marker.
        ({"product": ["tRNA-Leu"], "anticodon": ["TAA"]}, None),
        # Without the amino acid label the anticodon evidence is not consulted.
        ({"note": ["Anticodon: UAA"]}, None),
    ],
)
def test_anticodon_evidence_disambiguates_leucine_and_serine(qualifiers, expected):
    f = feature("tRNA-Leu(UUR)", 0, 70)
    f.qualifiers = qualifiers
    assert identify_region(f) == expected


def test_alias_evidence_wins_over_note_evidence():
    f = feature("ND4", 0, 70)
    f.qualifiers = {"gene": ["ND4"], "note": ["ND4L region"]}
    assert identify_region(f) == "ND4"


def test_note_evidence_is_used_only_when_no_alias_matches():
    f = feature("ND4", 0, 70)
    f.qualifiers = {"product": ["hypothetical protein"], "note": ["similar to ND4"]}
    assert identify_region(f) == "ND4"


def test_two_distinct_identities_are_ambiguous():
    f = feature("gene", 0, 70)
    f.qualifiers = {"gene": ["COI;ND4"]}
    assert identify_region(f) is None
    f.qualifiers = {"note": ["ND4L and ND4"], "product": ["hypothetical"]}
    assert identify_region(f) is None


def test_dloop_feature_type_is_an_identity_by_itself():
    f = feature("control_region", 0, 70)
    f.qualifiers = {}
    assert identify_region(f) == "control_region"


@pytest.mark.parametrize(
    ("region", "mutate", "reason"),
    [
        ("ND4", "codon_start", "unsupported_codon_start"),
        ("ND4", "conflicting_translation", "conflicting_translation"),
        ("ND4", "ambiguous_bases", "ambiguous_coding_sequence"),
        ("ND4", "overlapping_flanks", "overlapping_flanks"),
    ],
)
def test_repair_failure_reasons(region: str, mutate: str, reason: str):
    rec, _ = synthetic(region)
    if mutate == "codon_start":
        rec.features[-1].qualifiers.update(translation=["M" + "K" * 8], codon_start=["2"])
    elif mutate == "conflicting_translation":
        rec.features[-1].qualifiers.update(translation=["M" + "K" * 8])
        extra = feature(region, 10, 12)
        extra.qualifiers.update(translation=["MZZ"])
        rec.features.append(extra)
    elif mutate == "ambiguous_bases":
        rec.features[-1].qualifiers.update(translation=["M" + "K" * 8])
        bases = list(str(rec.seq))
        bases[100] = "N"
        rec.seq = Seq("".join(bases))
    elif mutate == "overlapping_flanks":
        index = ORDER.index(region)
        rec.features[1] = feature(ORDER[index - 1], 60, 120)
        rec.features[2] = feature(ORDER[index + 1], 100, 140)
    results, events = resolve_regions(rec, [region])
    assert results == []
    assert events[-1]["reason"] == reason

"""Region repair checks use synthetic mitochondrial feature maps."""

import csv
import io
import logging
from copy import deepcopy
from threading import Lock

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import AfterPosition, BeforePosition, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord
from rich.progress import Progress

from taxondbbuilder.cli import _build_ncbi_marker_rule
from taxondbbuilder.fasta import write_region_fallback_tsv
from taxondbbuilder.fasta import emit_records_to_fasta
from taxondbbuilder.markers import normalize_marker_map
from taxondbbuilder.mitogenome import (
    CDS_REGIONS, NEGATIVE, ORDER, identify_region, location_text, resolve_regions,
)
from taxondbbuilder.models import canonical_record_from_dict, canonical_record_to_dict
from taxondbbuilder.ncbi import extract_ncbi_records_from_genbank_chunk


def feature(region, start, end, strand=None, **qualifiers):
    strand = (-1 if region in NEGATIVE else 1) if strand is None else strand
    kind = "CDS" if region in CDS_REGIONS else "tRNA" if region.startswith("tRNA") else "D-loop" if region == "control_region" else "rRNA"
    return SeqFeature(SimpleLocation(start, end, strand=strand), type=kind,
                      qualifiers={"product": [region], **qualifiers})


def synthetic(region, coding="ATG" + "AAA" * 8 + "TAA", overlap=0):
    rec = SeqRecord(Seq("C" * 200), id="synthetic.1", name="synthetic")
    rec.annotations.update(molecule_type="DNA", topology="circular", taxonomy=["Metazoa", "Vertebrata"], organism="Example fish")
    left, right = ORDER[ORDER.index(region) - 1], ORDER[(ORDER.index(region) + 1) % len(ORDER)]
    start, end = 80 - overlap, 80 - overlap + len(coding)
    bases = list(str(rec.seq))
    bases[start:end] = str(Seq(coding).reverse_complement()) if region in NEGATIVE else coding
    rec.seq = Seq("".join(bases))
    rec.features = [
        SeqFeature(SimpleLocation(0, len(rec)), type="source", qualifiers={"organelle": ["mitochondrion"]}),
        feature(left, 50, 80), feature(right, end - overlap, end - overlap + 30),
        feature(region, end - 1 if region in NEGATIVE else start,
                end if region in NEGATIVE else start + 1),
    ]
    return rec, SimpleLocation(start, end, strand=-1 if region in NEGATIVE else 1)


def rule(targets, key="mitogenome"):
    cfg = normalize_marker_map({key: {"phrases": targets, "feature_types": [],
                                      "fallback": "mitogenome", "fallback_targets": targets}})[key]
    return _build_ncbi_marker_rule(key, cfg, "{acc_id}|{label}|{loc}|{strand}")


def extract(rec, rules):
    body = io.StringIO()
    SeqIO.write(rec, body, "genbank")
    counters = dict.fromkeys(["total_records", "matched_records", "matched_features", "skipped_same", "duplicated_diff"], 0)
    log = io.StringIO()
    logger = logging.Logger("test-fallback")
    logger.addHandler(logging.StreamHandler(log))
    progress = Progress(disable=True)
    records = extract_ncbi_records_from_genbank_chunk(body.getvalue(), rules, {}, counters, {}, Lock(),
                                                     progress, progress.add_task("test"), "1", None, run_logger=logger)
    return records, log.getvalue(), counters


def test_inferred_dloop_region_and_outputs(tmp_path):
    rec, expected = synthetic("control_region")
    records, log, counters = extract(rec, [rule(["control_region"])])
    assert len(records) == 1
    result = records[0]
    assert result.sequence == str(expected.extract(rec.seq)).upper()
    assert len(result.sequence) == len(expected)
    assert result.metadata["inferred_location"] == location_text(expected)
    assert result.metadata["region_id"] == "control_region"
    assert "status=inferred" in log and "single_base_location" in log
    assert counters["fallback_inferred"] == 1
    result = canonical_record_from_dict(canonical_record_to_dict(result))
    path, count = write_region_fallback_tsv(tmp_path / "test.fasta", [result])
    rows = list(csv.DictReader(path.open(), delimiter="\t"))
    assert count == 1 and rows[0]["acc_id"] == result.header_values["acc_id"]
    assert rows[0]["stage"] == "extraction_before_post_prep"
    assert rows[0]["inferred_length"] == str(len(expected))
    fasta = io.StringIO()
    emit_records_to_fasta([result], fasta, {"kept_records": 0}, [], Lock())
    assert fasta.getvalue().splitlines()[0].endswith("|region=control_region")
    write_region_fallback_tsv(tmp_path / "test.fasta", [])
    assert len(path.read_text().splitlines()) == 1


def test_full_marker_returns_only_complete_mitochondrial_record():
    rec, _ = synthetic("control_region")
    rec.description = "Example fish complete mitochondrial genome"
    full_cfg = normalize_marker_map({"Full": {
        "full_record": True,
        "phrases": ["complete mitochondrial genome"],
        "feature_types": [],
        "feature_fields": ["gene"],
    }})["Full"]
    full_rule = _build_ncbi_marker_rule("Full", full_cfg, "{acc_id}|{label}|{loc}")
    records, _, _ = extract(rec, [full_rule])
    assert len(records) == 1
    assert records[0].sequence == str(rec.seq).upper()
    assert records[0].header_values["start"] == "1"
    assert records[0].header_values["end"] == str(len(rec.seq))

    rec.description = "Example fish mitochondrial sequence"
    records, _, _ = extract(rec, [full_rule])
    assert records == []


@pytest.mark.parametrize("region", sorted(CDS_REGIONS))
@pytest.mark.parametrize("overlap", [0, 3])
def test_all_cds_translation_guided(region, overlap):
    rec, expected = synthetic(region, overlap=overlap)
    rec.features[-1].qualifiers.update(translation=["M" + "K" * 8], transl_table=["2"])
    results, events = resolve_regions(rec, [region])
    assert len(results) == 1, events
    _, repaired, metadata = results[0]
    assert repaired.location == expected
    assert str(repaired.extract(rec.seq)) == "ATG" + "AAA" * 8 + "TAA"
    assert metadata["extraction_method"] == "translation_guided"
    assert metadata["overlap_bases"] == f"{overlap},{overlap}"


@pytest.mark.parametrize("region", ["12S", "16S", "control_region", "tRNA-Val"])
def test_rna_and_control_region(region):
    rec, expected = synthetic(region)
    results, events = resolve_regions(rec, [region])
    assert len(results) == 1, events
    assert results[0][1].location == expected


@pytest.mark.parametrize("region", ["ND4", "ND6", "12S", "control_region"])
def test_reverse_registered_genome(region):
    rec, expected = synthetic(region)
    rec.features[-1].qualifiers.update(translation=["M" + "K" * 8], transl_table=["2"])
    reverse = rec.reverse_complement(id=True, name=True, annotations=True)
    results, events = resolve_regions(reverse, [region])
    assert len(results) == 1, events
    assert str(results[0][1].extract(reverse.seq)) == str(expected.extract(rec.seq))


def test_wraparound_and_empty_prefix():
    rec, _ = synthetic("control_region")
    rec.features[1] = feature("tRNA-Pro", 150, 180)
    rec.features[2] = feature("tRNA-Phe", 20, 50)
    rec.features[3] = feature("control_region", 180, 181)
    results, events = resolve_regions(rec, ["control_region"])
    assert len(results) == 1, events
    loc = results[0][1].location
    assert location_text(loc) == "join(181..200,1..20)"
    assert str(loc.extract(rec.seq)) == str(rec.seq[180:] + rec.seq[:20])
    rec.annotations["topology"] = "linear"
    results, events = resolve_regions(rec, ["control_region"])
    assert not results and events[0]["reason"] == "unsupported_topology"


@pytest.mark.parametrize("mutation,reason", [
    ("missing", "missing_flank"), ("duplicate", "ambiguous_flanks"),
    ("conflict", "conflicting_annotation"), ("intervening", "intervening_gene"),
    ("context", "unsupported_context"), ("strand", "unexpected_strands"),
])
def test_unsafe_repairs_are_skipped(mutation, reason):
    rec, _ = synthetic("control_region")
    if mutation == "missing":
        rec.features.pop(1)
    elif mutation == "duplicate":
        rec.features.append(feature("tRNA-Pro", 10, 40))
    elif mutation == "conflict":
        rec.features[-1].location = SimpleLocation(10, 11)
    elif mutation == "intervening":
        rec.features.append(feature("ND1", 90, 105))
    elif mutation == "context":
        rec.features[0].qualifiers["organelle"] = ["chloroplast"]
    elif mutation == "strand":
        rec.features[1].location = SimpleLocation(50, 80, strand=1)
    results, events = resolve_regions(rec, ["control_region"])
    assert not results and events[0]["reason"] == reason


def test_normal_alternate_feature_wins_independent_of_order():
    rec, expected = synthetic("ND4")
    rec.features.append(feature("ND4", expected.start, expected.end))
    for features in [rec.features, list(reversed(rec.features))]:
        rec.features = features
        results, events = resolve_regions(rec, ["ND4"])
        assert len(results) == 1 and results[0][1].location == expected
        assert results[0][2]["extraction_method"] == "alternate_feature"


def test_partial_is_repaired_or_retained_with_provenance():
    rec, _ = synthetic("12S")
    rec.features[-1].location = SimpleLocation(85, AfterPosition(105), strand=1)
    results, _ = resolve_regions(rec, ["12S"])
    assert results[0][1].location == SimpleLocation(85, 110, strand=1)
    rec.features.pop(1)
    results, _ = resolve_regions(rec, ["12S"])
    assert results[0][2]["status"] == "partial_unresolved"
    assert isinstance(results[0][1].location.end, AfterPosition)


def test_identity_does_not_use_substrings():
    for region in ["ND4", "ND4L", "COI", "COII", "COIII"]:
        assert identify_region(feature(region, 0, 20)) == region
    f = feature("ND4", 0, 20)
    f.qualifiers = {"note": ["ND4 and ND4L"]}
    assert identify_region(f) is None
    f.qualifiers = {"product": ["tRNA-Leu"]}
    assert identify_region(f) is None


def test_individual_and_grouped_extraction_agree():
    rec, expected = synthetic("ND4")
    rec.features.append(feature("COI", 150, 180))
    grouped, _, _ = extract(rec, [rule(["COI", "ND4"])])
    individual, _, _ = extract(rec, [rule(["ND4"], key="nd4")])
    assert len(grouped) == 2 and len(individual) == 1
    repaired = next(r for r in grouped if r.marker_label == "ND4")
    assert repaired.sequence == individual[0].sequence == str(expected.extract(rec.seq))
    assert repaired.metadata["inferred_location"] == individual[0].metadata["inferred_location"]


@pytest.mark.parametrize("fallback,targets", [("other", ["ND4"]), ([], ["ND4"]), ("mitogenome", []), ("mitogenome", ["ND"]), ("mitogenome", "ND4")])
def test_invalid_fallback_config(fallback, targets):
    import typer
    with pytest.raises(typer.BadParameter):
        normalize_marker_map({"test": {"phrases": ["ND4"], "fallback": fallback, "fallback_targets": targets}})


def test_unsupported_translation_exception_is_not_silently_ignored():
    rec, _ = synthetic("ND4")
    rec.features[-1].qualifiers["transl_except"] = ["(pos:1,aa:Sec)"]
    results, events = resolve_regions(rec, ["ND4"])
    assert not results and events[0]["reason"] == "unsupported_translation_exception"


def test_gene_span_requires_cds_boundary_evidence():
    rec, expected = synthetic("ND4")
    gene = feature("ND4", 75, 115)
    gene.type = "gene"
    rec.features.append(gene)
    results, events = resolve_regions(rec, ["ND4"])
    assert not results, "A broad gene annotation must not bypass CDS validation"
    gene.location = expected
    results, events = resolve_regions(rec, ["ND4"])
    assert len(results) == 1, events
    assert results[0][1].location == expected


def test_identify_leucine_by_anticodon():
    f = feature("tRNA-Leu(UUR)", 0, 70)
    f.qualifiers = {"product": ["tRNA-Leu"], "note": ["Anticodon: UAA"]}
    assert identify_region(f) == "tRNA-Leu(UUR)"


def test_cli_repair_workers_and_repeat(tmp_path):
    from pathlib import Path
    from typer.testing import CliRunner
    from taxondbbuilder.cli import app

    gb_dir = tmp_path / "gb"
    gb_dir.mkdir()
    rec, expected = synthetic("control_region")
    SeqIO.write(rec, gb_dir / "example.gb", "genbank")
    markers = Path(__file__).resolve().parents[1] / "configs" / "markers_mitogenome.toml"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[ncbi]\nemail = \"test@example.invalid\"\n[markers]\nfile = \"{markers}\"\n"
        "[output.header_formats]\nmifish_pipeline = \"{acc_id}|{organism}\"\n"
    )
    output = tmp_path / "out.fasta"
    previous = None
    for workers in [1, 2]:
        result = CliRunner().invoke(app, ["build", "-c", str(config), "-t", "30991", "-m", "control_region",
                                          "--from-gb", str(gb_dir), "--out", str(output), "--workers", str(workers)])
        assert result.exit_code == 0, result.output
        fasta_records = list(SeqIO.parse(output, "fasta"))
        assert len(fasta_records) == 1
        assert len(fasta_records[0]) == len(expected)
        assert fasta_records[0].description.endswith("|region=control_region")
        tsv = output.with_suffix(".fasta.region_fallback.tsv")
        rows = list(csv.DictReader(tsv.open(), delimiter="\t"))
        assert len(rows) == 1
        assert rows[0]["region_id"] == "control_region"
        assert rows[0]["inferred_length"] == str(len(expected))
        assert rows[0]["header"] == fasta_records[0].description
        assert "status=inferred" in output.with_suffix(".fasta.log").read_text()
        current = (output.read_text(), tsv.read_text())
        if previous:
            assert previous == current
        previous = current


def test_unlabelled_dloop_and_missing_feature():
    rec, expected = synthetic("control_region")
    rec.features[-1].qualifiers = {}
    results, events = resolve_regions(rec, ["control_region"])
    assert len(results) == 1 and results[0][1].location == expected, events
    rec.features.pop()
    results, events = resolve_regions(rec, ["control_region"])
    assert len(results) == 1 and results[0][2]["fallback_reason"] == "missing_feature", events


def test_translation_mismatch_and_ambiguous_candidates():
    rec, _ = synthetic("ND4")
    rec.features[-1].qualifiers["translation"] = ["MWWWWWWWW"]
    results, events = resolve_regions(rec, ["ND4"])
    assert not results and events[-1]["reason"] == "insufficient_cds_evidence"
    rec.features[-1].location = None
    rec.seq = Seq("C" * 50 + ("ATG" + "AAA" * 4 + "TAA") * 2 + "C" * 114)
    rec.features[-1].qualifiers["translation"] = ["MKKKK"]
    results, events = resolve_regions(rec, ["ND4"])
    assert not results and events[-1]["reason"] == "ambiguous_cds_boundaries"


def test_normal_output_is_unchanged_when_fallback_is_enabled():
    rec, expected = synthetic("ND4")
    rec.features[-1].location = expected
    rec.features[-1].qualifiers["gene"] = ["ND4"]
    enabled = rule(["ND4"])
    enabled["feature_fields"] = ["gene"]
    disabled = {**enabled, "fallback": "none"}
    a, _, _ = extract(rec, [enabled])
    b, _, _ = extract(rec, [disabled])
    assert [(r.sequence, r.header_values) for r in a] == [(r.sequence, r.header_values) for r in b]


def test_fuzzy_and_duplicate_flanks():
    rec, _ = synthetic("12S")
    rec.features.append(deepcopy(rec.features[1]))
    results, events = resolve_regions(rec, ["12S"])
    assert len(results) == 1, events
    for f in rec.features:
        if identify_region(f) == "tRNA-Phe":
            f.location = SimpleLocation(BeforePosition(50), 80, strand=1)
    results, events = resolve_regions(rec, ["12S"])
    assert not results and events[-1]["reason"] == "missing_flank"



def test_cds_only_rule_uses_cds_instead_of_duplicate_gene():
    rec, expected = synthetic("ND1")
    rec.features[-1].location = expected
    duplicate_gene = feature("ND1", int(expected.start), int(expected.end))
    duplicate_gene.type = "gene"
    rec.features.append(duplicate_gene)
    config = rule(["ND1"])
    config["feature_types"] = ["CDS"]
    results, _, _ = extract(rec, [config])
    assert len(results) == 1 and results[0].header_values["type"] == "CDS"



def test_packaged_mitogenome_fallback(tmp_path):
    """A stale GUI sidecar must not silently emit a one-base control region."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sidecar = root / "dist" / "taxondbbuilder"
    if not sidecar.is_file():
        pytest.skip("Build the GUI sidecar to run the packaging regression check")
    gb_dir = tmp_path / "gb"
    gb_dir.mkdir()
    rec, expected = synthetic("control_region")
    SeqIO.write(rec, gb_dir / "record.gb", "genbank")
    config = tmp_path / "config.toml"
    config.write_text(
        '[ncbi]\nemail = "test@example.invalid"\n[markers]\nfile = "'
        + str(root / "configs" / "markers_mitogenome.toml") + '"\n'
        + '[output.header_formats]\nmifish_pipeline = "{acc_id}|{organism}"\n'
    )
    output = tmp_path / "packaged.fasta"
    result = subprocess.run(
        [str(sidecar), "build", "-c", str(config), "-t", "30991", "-m", "control_region",
         "--source", "ncbi", "--from-gb", str(gb_dir), "--out", str(output)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = list(SeqIO.parse(output, "fasta"))
    assert len(records) == 1 and len(records[0]) == len(expected)
    with output.with_suffix(".fasta.region_fallback.tsv").open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert rows[0]["inferred_location"] == location_text(expected)
    assert "status=inferred" in output.with_suffix(".fasta.log").read_text()

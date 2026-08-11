from __future__ import annotations

import io
import json
from pathlib import Path
from threading import Lock

import pytest
from rich.progress import Progress

from .conftest import json_text, read_golden


def _records_for_output(builder):
    return [
        builder.CanonicalRecord(
            source="ncbi",
            source_record_id="AB123.1",
            accession="AB123.1",
            processid=None,
            sampleid=None,
            taxon_name="Alpha fish",
            marker_key="coi",
            marker_label="COI",
            sequence="AACCGGTT",
            header_values={
                "acc_id": "AB123.1",
                "organism_raw": "Alpha fish",
                "organism": "Alpha_fish",
                "marker": "coi",
            },
            metadata={"header_format": "{acc_id}|{organism_raw}|{marker}"},
        ),
        builder.CanonicalRecord(
            source="bold",
            source_record_id="PRC002",
            accession="GB456",
            processid="PRC002",
            sampleid="SAM002",
            taxon_name="Beta fish",
            marker_key="coi",
            marker_label="COI-5P",
            sequence="AACCGGTT",
            header_values={
                "acc_id": "BOLD_PRC002",
                "organism_raw": "Beta fish",
                "organism": "Beta_fish",
                "marker": "coi",
            },
            metadata={"header_format": "{acc_id}|{organism_raw}|{marker}"},
        ),
        builder.CanonicalRecord(
            source="ncbi",
            source_record_id="CD789.1",
            accession="CD789.1",
            processid=None,
            sampleid=None,
            taxon_name="Gamma fish",
            marker_key="12s",
            marker_label="12S",
            sequence="TTTTGGGG",
            header_values={
                "acc_id": "CD789.1",
                "organism_raw": "Gamma fish",
                "organism": "Gamma_fish",
                "marker": "12s",
            },
            metadata={"header_format": "{acc_id}|{organism_raw}|{marker}"},
        ),
    ]


def test_extract_ncbi_records_from_minimal_genbank_fixture(
    fixture_dir: Path, golden_dir: Path
):
    import taxondbbuilder as builder

    raw_markers = {
        "12s": {
            "phrases": ["12S"],
            "feature_types": ["gene"],
            "feature_fields": ["gene"],
            "header_format": "{acc_id}|{marker}|{loc}",
        },
        "16s": {
            "phrases": ["16S"],
            "feature_types": ["rRNA"],
            "feature_fields": ["product"],
            "header_format": "{acc_id}|{marker}|{loc}",
        },
        "coi": {
            "phrases": ["COI"],
            "feature_types": ["gene"],
            "feature_fields": ["gene"],
            "header_format": "{acc_id}|{marker}|{loc}",
        },
    }
    marker_map = builder.normalize_marker_map(raw_markers)
    rules = [
        {
            "key": key,
            "patterns": builder.compile_patterns(builder.build_region_patterns(cfg)),
            "feature_types": cfg["feature_types"] or [],
            "feature_fields": cfg["feature_fields"] or builder.DEFAULT_FEATURE_FIELDS,
            "header_format": cfg["header_format"],
        }
        for key, cfg in marker_map.items()
    ]

    counters = {
        key: 0
        for key in (
            "total_records",
            "matched_features",
            "skipped_same",
            "duplicated_diff",
            "matched_records",
        )
    }
    acc_to_seqs = {}
    dup_accessions = {}
    progress = Progress()
    task_id = progress.add_task("fixture", total=2)
    records = builder.extract_ncbi_records_from_genbank_chunk(
        (fixture_dir / "sample.gb").read_text(encoding="utf-8"),
        rules,
        acc_to_seqs,
        counters,
        dup_accessions,
        Lock(),
        progress,
        task_id,
        "999",
        None,
    )
    actual = {
        "counters": counters,
        "duplicates": dup_accessions,
        "records": [builder.canonical_record_to_dict(r) for r in records],
    }
    assert json_text(actual) == read_golden(golden_dir, "genbank-records.json")


def test_ncbi_record_taxonomy_uses_source_taxid_and_lineage():
    from Bio.Seq import Seq
    from Bio.SeqFeature import FeatureLocation, SeqFeature
    from Bio.SeqRecord import SeqRecord

    from taxondbbuilder.ncbi import _record_taxonomy

    record = SeqRecord(Seq("ACGT"))
    record.features = [
        SeqFeature(
            FeatureLocation(0, 4),
            type="source",
            qualifiers={"db_xref": ["BioProject:1", "taxon:123"]},
        )
    ]
    record.annotations["taxonomy"] = ["Eukaryota", "Metazoa"]
    assert _record_taxonomy(record) == ("123", ["Eukaryota", "Metazoa"])


def test_emit_and_sidecar_csv_outputs_match_golden(tmp_path: Path, golden_dir: Path):
    import taxondbbuilder as builder

    fasta_path = tmp_path / "sample.fasta"
    output = io.StringIO()
    counters = {"kept_records": 0}
    emitted = []
    merge_rows = []
    builder.emit_records_to_fasta(
        _records_for_output(builder), output, counters, emitted, Lock(), merge_rows
    )
    fasta_path.write_text(output.getvalue(), encoding="utf-8")
    assert output.getvalue() == read_golden(golden_dir, "records.fasta")
    assert counters == {"kept_records": 3}
    assert emitted == merge_rows

    merge_path = builder.write_source_merge_csv(fasta_path, merge_rows)
    mapping_path, mapping_stats = builder.write_acc_organism_mapping_csv(
        fasta_path, emitted
    )
    duplicate_records_path, duplicate_groups_path, duplicate_stats, duplicate_error = (
        builder.write_duplicate_acc_reports_csv(
            fasta_path, ["{acc_id}|{organism_raw}|{marker}"]
        )
    )
    assert duplicate_error is None
    assert merge_path.read_text(encoding="utf-8") == read_golden(
        golden_dir, "records.source_merge.csv"
    )
    assert mapping_path.read_text(encoding="utf-8") == read_golden(
        golden_dir, "records.acc_organism.csv"
    )
    assert duplicate_records_path.read_text(encoding="utf-8") == read_golden(
        golden_dir, "records.duplicate_acc.records.csv"
    )
    assert duplicate_groups_path.read_text(encoding="utf-8") == read_golden(
        golden_dir, "records.duplicate_acc.groups.csv"
    )
    assert json_text(
        {"mapping": mapping_stats, "duplicate": duplicate_stats}
    ) == read_golden(golden_dir, "records.sidecar-stats.json")


def test_length_filter_characterization(tmp_path: Path):
    import taxondbbuilder as builder

    path = tmp_path / "lengths.fasta"
    path.write_text(">short\nAAA\n>keep\nAAAA\n>long\nAAAAAA\n", encoding="utf-8")
    assert builder.apply_post_prep_length_filter(path, 4, 5) == {
        "before": 3,
        "after": 1,
        "removed": 2,
    }
    assert path.read_text(encoding="utf-8") == ">keep\nAAAA\n"


def test_interoperability_exports_preserve_primary_fasta(tmp_path: Path):
    import taxondbbuilder as builder

    fasta_path = tmp_path / "sample.fasta"
    fasta_text = (
        ">gb|AB123.1|Alpha_fish\nAACCGGTT\n"
        ">bold|BOLD_PRC002|unknown\nTTTTGGGG\n"
        ">gb|AB123.1_dup|Beta_fish\nCCCCAAAA\n"
    )
    fasta_path.write_text(fasta_text, encoding="utf-8")
    emitted = [
        {
            "header": "gb|AB123.1|Alpha_fish",
            "acc_id": "AB123.1",
            "organism_name": "Alpha fish",
            "taxonomy_lineage": "Eukaryota; Metazoa",
        },
        {
            "header": "bold|BOLD_PRC002|unknown",
            "acc_id": "BOLD_PRC002",
            "organism_name": "unknown",
        },
        {
            "header": "gb|AB123.1_dup|Beta_fish",
            "acc_id": "AB123.1",
            "organism_name": "Beta fish subspecies",
        },
    ]

    qiime2_result = builder.write_interoperability_exports(
        fasta_path, emitted, [builder.ExportFormat.QIIME2]
    )[0]
    dada2_result = builder.write_interoperability_exports(
        fasta_path, emitted, [builder.ExportFormat.DADA2_SPECIES]
    )[0]

    assert fasta_path.read_text(encoding="utf-8") == fasta_text
    assert qiime2_result["exported_records"] == 3
    assert qiime2_result["skipped_records"] == 0
    assert dada2_result["exported_records"] == 2
    assert dada2_result["skipped_records"] == 1
    assert (
        tmp_path / "sample.fasta.qiime2.sequences.fasta"
    ).read_text(encoding="utf-8") == (
        ">AB123.1\nAACCGGTT\n"
        ">BOLD_PRC002\nTTTTGGGG\n"
        ">AB123.1__2\nCCCCAAAA\n"
    )
    assert (tmp_path / "sample.fasta.qiime2.taxonomy.tsv").read_text(
        encoding="utf-8"
    ) == (
        "Feature ID\tTaxon\n"
        "AB123.1\tEukaryota; Metazoa; Alpha fish\n"
        "BOLD_PRC002\tunknown\n"
        "AB123.1__2\tBeta fish subspecies\n"
    )
    assert (tmp_path / "sample.fasta.dada2.species.fasta").read_text(
        encoding="utf-8"
    ) == (
        ">AB123.1 Alpha fish\nAACCGGTT\n"
        ">AB123.1__2 Beta fish\nCCCCAAAA\n"
    )
    with pytest.raises(ValueError, match="Only one output export format"):
        builder.write_interoperability_exports(
            fasta_path,
            emitted,
            [builder.ExportFormat.QIIME2, builder.ExportFormat.DADA2_SPECIES],
        )


def test_quality_filter_rejects_invalid_ambiguous_and_conflicting_sequences(
    tmp_path: Path,
) -> None:
    import taxondbbuilder as builder

    fasta_path = tmp_path / "quality.fasta"
    fasta_path.write_text(
        ">alpha\nACGT\n"
        ">beta\nACGT\n"
        ">ambiguous\nACGTN\n"
        ">invalid\nACGTX\n"
        ">kept\nAACCGG\n",
        encoding="utf-8",
    )
    emitted = [
        {"header": "alpha", "organism_name": "Alpha fish"},
        {"header": "beta", "organism_name": "Beta fish"},
        {"header": "ambiguous", "organism_name": "Alpha fish"},
        {"header": "invalid", "organism_name": "Alpha fish"},
        {"header": "kept", "organism_name": "Gamma fish"},
    ]

    result = builder.apply_post_prep_quality_filter(
        fasta_path,
        emitted,
        max_ambiguous_fraction=0.1,
        reject_invalid_iupac=True,
        duplicate_policy="exclude_conflicts",
    )

    assert result["after"] == 1
    assert fasta_path.read_text(encoding="utf-8") == ">kept\nAACCGG\n"
    report = result["report_path"].read_text(encoding="utf-8")
    assert "taxonomy_conflict" in report
    assert "too_many_ambiguous_bases" in report
    assert "invalid_iupac:X" in report


def test_run_manifest_records_queries_and_output_hashes(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    config_path = tmp_path / "db.toml"
    fasta_path = tmp_path / "db.fasta"
    config_path.write_text("[markers]\n", encoding="utf-8")
    fasta_path.write_text(">id\nACGT\n", encoding="utf-8")

    manifest_path = builder.write_run_manifest(
        fasta_path,
        config_path,
        source="ncbi",
        taxon_inputs=["Testus alpha"],
        resolved_taxa=[
            builder.ResolvedTaxon("Testus alpha", "999", "Testus alpha")
        ],
        markers=["12s"],
        ncbi_queries=["txid999[Organism]"],
        bold_queries=[],
        output_paths=[fasta_path],
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["queries"]["ncbi"] == ["txid999[Organism]"]
    assert payload["outputs"][0]["sha256"] == (
        "2b0cb032faef8581ff7c3bc62ead49b29ca475bff579322d17c23088f1369c7d"
    )

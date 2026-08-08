from __future__ import annotations

from pathlib import Path

import pytest

from taxondbbuilder.postprep import phylo
from taxondbbuilder.postprep.phylo import (
    apply_post_prep_msa_tree,
    run_msa,
    run_tree,
)


def _write_fasta(path: Path, record_count: int) -> None:
    path.write_text(
        "".join(f">taxon_{index}\nACGT\n" for index in range(record_count)),
        encoding="utf-8",
    )


def _options(*, min_taxa: int = 3, max_samples: int = 500) -> dict[str, object]:
    return {
        "min_taxa": min_taxa,
        "max_samples": max_samples,
        "model": "GTR+G",
        "bootstrap_replicates": 1000,
    }


def test_apply_post_prep_msa_tree_skips_too_few_taxa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "too-few.fasta"
    _write_fasta(fasta_path, 2)

    def unexpected_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MSA and tree building must not run for too few taxa")

    monkeypatch.setattr(phylo, "run_msa", unexpected_call)
    monkeypatch.setattr(phylo, "run_tree", unexpected_call)

    result = apply_post_prep_msa_tree(fasta_path, _options(min_taxa=3))

    assert result == {
        "status": "skipped_too_few_taxa",
        "taxa_count": 2,
        "msa_path": None,
        "tree_path": None,
    }


def test_apply_post_prep_msa_tree_skips_too_many_taxa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "too-many.fasta"
    _write_fasta(fasta_path, 4)

    def unexpected_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MSA and tree building must not run for too many taxa")

    monkeypatch.setattr(phylo, "run_msa", unexpected_call)
    monkeypatch.setattr(phylo, "run_tree", unexpected_call)

    result = apply_post_prep_msa_tree(fasta_path, _options(max_samples=3))

    assert result == {
        "status": "skipped_too_many_taxa",
        "taxa_count": 4,
        "msa_path": None,
        "tree_path": None,
    }


def test_apply_post_prep_msa_tree_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "kept.fasta"
    _write_fasta(fasta_path, 3)
    calls: dict[str, object] = {}

    aligned_records = [
        ("taxon_0", "ACGT-"),
        ("taxon_1", "ACGTA"),
        ("taxon_2", "AC-T-"),
    ]

    def fake_msa(
        records: list[tuple[str, str]],
    ) -> tuple[list[tuple[str, str]], None]:
        calls["msa_records"] = records
        return aligned_records, None

    def fake_tree(
        records: list[tuple[str, str]], model: str, bootstrap_replicates: int
    ) -> tuple[str, None]:
        calls["tree_records"] = records
        calls["model"] = model
        calls["bootstrap_replicates"] = bootstrap_replicates
        return "(taxon_0,taxon_1,taxon_2);\n", None

    monkeypatch.setattr(phylo, "run_msa", fake_msa)
    monkeypatch.setattr(phylo, "run_tree", fake_tree)

    result = apply_post_prep_msa_tree(
        fasta_path, _options(min_taxa=2, max_samples=4)
    )

    msa_path = Path(str(result["msa_path"]))
    tree_path = Path(str(result["tree_path"]))
    assert result["status"] == "ok"
    assert result["taxa_count"] == 3
    assert msa_path.read_text(encoding="utf-8") == (
        ">taxon_0\nACGT-\n>taxon_1\nACGTA\n>taxon_2\nAC-T-\n"
    )
    assert tree_path.read_text(encoding="utf-8") == "(taxon_0,taxon_1,taxon_2);\n"
    assert calls["msa_records"] == [
        ("taxon_0", "ACGT"),
        ("taxon_1", "ACGT"),
        ("taxon_2", "ACGT"),
    ]
    assert calls["tree_records"] == aligned_records
    assert calls["model"] == "GTR+G"
    assert calls["bootstrap_replicates"] == 1000


def test_apply_post_prep_msa_tree_reports_msa_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "msa-failure.fasta"
    _write_fasta(fasta_path, 3)

    monkeypatch.setattr(phylo, "run_msa", lambda _records: (None, "msa_failed"))

    def unexpected_tree(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tree building must not run after MSA failure")

    monkeypatch.setattr(phylo, "run_tree", unexpected_tree)

    result = apply_post_prep_msa_tree(fasta_path, _options())

    assert result == {
        "status": "msa_failed",
        "taxa_count": 3,
        "msa_path": None,
        "tree_path": None,
    }


def test_apply_post_prep_msa_tree_keeps_msa_after_tree_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "tree-failure.fasta"
    _write_fasta(fasta_path, 3)
    aligned_records = [(f"taxon_{index}", "ACGT") for index in range(3)]

    monkeypatch.setattr(
        phylo, "run_msa", lambda _records: (aligned_records, None)
    )
    monkeypatch.setattr(
        phylo,
        "run_tree",
        lambda _records, _model, _bootstrap: (None, "tree_failed"),
    )

    result = apply_post_prep_msa_tree(fasta_path, _options())

    assert result["status"] == "tree_failed"
    assert result["taxa_count"] == 3
    assert result["msa_path"] == str(fasta_path) + ".msa.fasta"
    assert Path(str(result["msa_path"])).exists()
    assert result["tree_path"] is None


def test_apply_post_prep_msa_tree_builds_one_tree_per_taxid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "per-taxid.fasta"
    fasta_path.write_text(
        ">first\nACGT\n>second\nACGA\n>third\nACGG\n>fourth\nACGC\n",
        encoding="utf-8",
    )
    calls: list[list[tuple[str, str]]] = []

    monkeypatch.setattr(
        phylo,
        "run_msa",
        lambda records: ([(record_id, sequence + "-") for record_id, sequence in records], None),
    )

    def fake_tree(
        records: list[tuple[str, str]], _model: str, _bootstrap: int
    ) -> tuple[str, None]:
        calls.append(records)
        return "(" + ",".join(record_id for record_id, _ in records) + ");\n", None

    monkeypatch.setattr(phylo, "run_tree", fake_tree)

    result = apply_post_prep_msa_tree(
        fasta_path,
        _options(min_taxa=2, max_samples=3) | {"mode": "per_taxid"},
        taxid_by_header={
            "first": "100",
            "second": "100",
            "third": "200",
            "fourth": "200",
        },
    )

    assert result["status"] == "ok"
    assert result["taxa_count"] == 4
    assert [item["taxid"] for item in result["tree_outputs"]] == ["100", "200"]
    assert calls == [
        [("first", "ACGT-"), ("second", "ACGA-")],
        [("third", "ACGG-"), ("fourth", "ACGC-")],
    ]
    assert Path(str(result["tree_outputs"][0]["tree_path"])).name == (
        "per-taxid.fasta.taxid100.tree.nwk"
    )


def test_run_msa_preserves_record_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_align(sequences: list[str], *, seq_type: str) -> list[str]:
        calls["sequences"] = sequences
        calls["seq_type"] = seq_type
        return ["AC-GT", "ACGGT"]

    monkeypatch.setattr(phylo.kalign, "align", fake_align)

    assert run_msa([("first", "ACGT"), ("second", "ACGGT")]) == (
        [("first", "AC-GT"), ("second", "ACGGT")],
        None,
    )
    assert calls == {"sequences": ["ACGT", "ACGGT"], "seq_type": "dna"}


def test_run_msa_catches_library_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_align(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("bad alignment")

    monkeypatch.setattr(phylo.kalign, "align", fail_align)

    assert run_msa([("taxon", "ACGT")]) == (None, "msa_failed")


def test_run_tree_builds_newick(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}
    alignment = object()

    class FakeTree:
        def postorder(self) -> list[object]:
            return []

        def get_newick(self, *, with_distances: bool) -> str:
            calls["with_distances"] = with_distances
            return "(first:0.1,second:0.2);"

    def fake_make_aligned_seqs(data: dict[str, str], *, moltype: str) -> object:
        calls["data"] = data
        calls["moltype"] = moltype
        return alignment

    def fake_build_tree(
        aln: object,
        model: str,
        *,
        rand_seed: int,
        bootstrap_replicates: int,
    ) -> FakeTree:
        calls["alignment"] = aln
        calls["model"] = model
        calls["rand_seed"] = rand_seed
        calls["bootstrap_replicates"] = bootstrap_replicates
        return FakeTree()

    monkeypatch.setattr(phylo, "make_aligned_seqs", fake_make_aligned_seqs)
    monkeypatch.setattr(phylo.piqtree, "build_tree", fake_build_tree)

    assert run_tree([("first", "AC-GT"), ("second", "ACGGT")], "GTR+G") == (
        "(first:0.1,second:0.2);",
        None,
    )
    assert calls == {
        "data": {"first": "AC-GT", "second": "ACGGT"},
        "moltype": "dna",
        "alignment": alignment,
        "model": "GTR+G",
        "rand_seed": 1,
        "bootstrap_replicates": 1000,
        "with_distances": True,
    }


def test_run_tree_preserves_sequences_with_duplicate_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    alignment = object()

    def fake_make_aligned_seqs(data: dict[str, str], *, moltype: str) -> object:
        calls["data"] = data
        calls["moltype"] = moltype
        return alignment

    class FakeTree:
        def postorder(self) -> list[object]:
            return []

        def get_newick(self, *, with_distances: bool) -> str:
            return "(taxon,taxon__2,other);"

    monkeypatch.setattr(phylo, "make_aligned_seqs", fake_make_aligned_seqs)
    monkeypatch.setattr(
        phylo.piqtree,
        "build_tree",
        lambda *_args, **_kwargs: FakeTree(),
    )

    result = run_tree(
        [("taxon", "ACGT"), ("taxon", "ACGA"), ("other", "TTTT")],
        "GTR+G",
    )

    assert result == ("(taxon,taxon__2,other);", None)
    assert calls["data"] == {
        "taxon": "ACGT",
        "taxon__2": "ACGA",
        "other": "TTTT",
    }
    assert calls["moltype"] == "dna"


def test_run_tree_catches_library_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        phylo,
        "make_aligned_seqs",
        lambda _records, **_kwargs: object(),
    )

    def fail_build_tree(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("bad tree")

    monkeypatch.setattr(phylo.piqtree, "build_tree", fail_build_tree)

    assert run_tree([("taxon", "ACGT")], "GTR+G") == (None, "tree_failed")

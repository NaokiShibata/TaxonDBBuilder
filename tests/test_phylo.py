from __future__ import annotations

from pathlib import Path

import pytest

from taxondbbuilder.postprep import phylo
from taxondbbuilder.postprep.phylo import (
    apply_post_prep_msa_tree,
    run_iqtree,
    run_mafft,
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
    }


def test_apply_post_prep_msa_tree_skips_too_few_taxa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "too-few.fasta"
    _write_fasta(fasta_path, 2)

    def unexpected_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external phylogeny tools must not run for too few taxa")

    monkeypatch.setattr(phylo, "run_mafft", unexpected_call)
    monkeypatch.setattr(phylo, "run_iqtree", unexpected_call)

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
        raise AssertionError("external phylogeny tools must not run for too many taxa")

    monkeypatch.setattr(phylo, "run_mafft", unexpected_call)
    monkeypatch.setattr(phylo, "run_iqtree", unexpected_call)

    result = apply_post_prep_msa_tree(fasta_path, _options(max_samples=3))

    assert result == {
        "status": "skipped_too_many_taxa",
        "taxa_count": 4,
        "msa_path": None,
        "tree_path": None,
    }


def test_apply_post_prep_msa_tree_happy_path_stubs_external_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "kept.fasta"
    _write_fasta(fasta_path, 3)
    calls: dict[str, object] = {}

    def fake_mafft(input_path: Path, out_path: Path) -> None:
        calls["mafft_input"] = input_path
        out_path.write_text(">taxon_0\nACGT\n", encoding="utf-8")
        return None

    def fake_iqtree(
        msa_path: Path, out_dir: Path, model: str
    ) -> tuple[Path, None]:
        calls["iqtree_msa"] = msa_path
        calls["model"] = model
        out_dir.mkdir(parents=True, exist_ok=True)
        tree_path = out_dir / "fixture.treefile"
        tree_path.write_text("(taxon_0,taxon_1,taxon_2);\n", encoding="utf-8")
        return tree_path, None

    monkeypatch.setattr(phylo, "run_mafft", fake_mafft)
    monkeypatch.setattr(phylo, "run_iqtree", fake_iqtree)

    result = apply_post_prep_msa_tree(
        fasta_path, _options(min_taxa=2, max_samples=4)
    )

    assert result["status"] == "ok"
    assert result["taxa_count"] == 3
    assert result["msa_path"] is not None
    assert result["tree_path"] is not None
    assert Path(result["msa_path"]).exists()
    assert Path(result["tree_path"]).exists()
    assert calls["mafft_input"] == fasta_path
    assert calls["iqtree_msa"] == Path(result["msa_path"])
    assert calls["model"] == "GTR+G"


def test_apply_post_prep_msa_tree_propagates_missing_mafft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "missing-mafft.fasta"
    _write_fasta(fasta_path, 3)

    monkeypatch.setattr(phylo, "run_mafft", lambda *_args, **_kwargs: "mafft_not_found")

    def unexpected_iqtree(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("IQ-TREE must not run after MAFFT is unavailable")

    monkeypatch.setattr(phylo, "run_iqtree", unexpected_iqtree)

    result = apply_post_prep_msa_tree(fasta_path, _options())

    assert result["status"] == "mafft_not_found"
    assert result["taxa_count"] == 3
    assert result["msa_path"] is None
    assert result["tree_path"] is None


def test_run_mafft_returns_not_found_when_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fasta_path = tmp_path / "input.fasta"
    out_path = tmp_path / "alignment.fasta"
    _write_fasta(fasta_path, 3)
    monkeypatch.setattr(phylo.shutil, "which", lambda _name: None)

    assert run_mafft(fasta_path, out_path) == "mafft_not_found"
    assert not out_path.exists()


def test_run_iqtree_returns_not_found_when_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    msa_path = tmp_path / "alignment.fasta"
    _write_fasta(msa_path, 3)
    monkeypatch.setattr(phylo.shutil, "which", lambda _name: None)

    assert run_iqtree(msa_path, tmp_path / "tree", "GTR+G") == (
        None,
        "iqtree_not_found",
    )

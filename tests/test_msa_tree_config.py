from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer


def _write_config(path: Path, post_prep: str = "") -> None:
    path.write_text(
        "[ncbi]\n"
        "[markers.x]\n"
        "phrases = ['x']\n"
        "[post_prep]\n"
        f"{post_prep}",
        encoding="utf-8",
    )


def test_msa_tree_config_defaults_apply_when_keys_are_omitted(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "defaults.toml"
    _write_config(path)

    post_prep = builder.load_config(path)["post_prep"]

    assert post_prep["msa_tree_enable"] is False
    assert post_prep["msa_tree_min_taxa"] == 3
    assert post_prep["msa_tree_max_samples"] == 500
    assert post_prep["msa_tree_model"] == "GTR+G"
    assert post_prep["msa_tree_mode"] == "combined"
    assert post_prep["msa_tree_bootstrap_replicates"] == 1000


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("false", False),
        ("'YES'", True),
        ("'0'", False),
    ],
)
def test_msa_tree_enable_uses_existing_boolean_parsing_convention(
    tmp_path: Path, raw: str, expected: bool
) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "bool.toml"
    _write_config(path, f"msa_tree_enable = {raw}\n")

    assert builder.load_config(path)["post_prep"]["msa_tree_enable"] is expected


@pytest.mark.parametrize("raw", ["1", "'sometimes'"])
def test_msa_tree_enable_rejects_non_boolean_values(tmp_path: Path, raw: str) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "bad-bool.toml"
    _write_config(path, f"msa_tree_enable = {raw}\n")

    with pytest.raises(
        typer.BadParameter, match="post_prep.msa_tree_enable must be a boolean"
    ):
        builder.load_config(path)


@pytest.mark.parametrize(
    ("key", "raw", "message"),
    [
        ("msa_tree_min_taxa", "-1", "must be >= 0"),
        ("msa_tree_min_taxa", "'many'", "must be an integer"),
        ("msa_tree_max_samples", "-1", "must be >= 0"),
        ("msa_tree_max_samples", "'many'", "must be an integer"),
    ],
)
def test_msa_tree_counts_reject_invalid_values(
    tmp_path: Path, key: str, raw: str, message: str
) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "bad-count.toml"
    _write_config(path, f"{key} = {raw}\n")

    with pytest.raises(
        typer.BadParameter, match=rf"post_prep\.{key} {re.escape(message)}"
    ):
        builder.load_config(path)


def test_msa_tree_counts_accept_non_negative_integers(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "counts.toml"
    _write_config(
        path,
        "msa_tree_min_taxa = 0\n"
        "msa_tree_max_samples = 12\n",
    )

    post_prep = builder.load_config(path)["post_prep"]
    assert post_prep["msa_tree_min_taxa"] == 0
    assert post_prep["msa_tree_max_samples"] == 12


@pytest.mark.parametrize("raw", ["''", "'   '", "12"])
def test_msa_tree_model_rejects_empty_or_non_string_values(
    tmp_path: Path, raw: str
) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "bad-model.toml"
    _write_config(path, f"msa_tree_model = {raw}\n")

    with pytest.raises(typer.BadParameter, match="post_prep.msa_tree_model"):
        builder.load_config(path)


def test_msa_tree_model_accepts_a_non_empty_string(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "model.toml"
    _write_config(path, "msa_tree_model = 'HKY+G'\n")

    assert builder.load_config(path)["post_prep"]["msa_tree_model"] == "HKY+G"


@pytest.mark.parametrize("mode", ["combined", "per_taxid"])
def test_msa_tree_mode_accepts_supported_values(tmp_path: Path, mode: str) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "mode.toml"
    _write_config(path, f"msa_tree_mode = '{mode}'\n")

    assert builder.load_config(path)["post_prep"]["msa_tree_mode"] == mode


def test_msa_tree_mode_rejects_unknown_value(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "bad-mode.toml"
    _write_config(path, "msa_tree_mode = 'per_marker'\n")

    with pytest.raises(typer.BadParameter, match="post_prep.msa_tree_mode"):
        builder.load_config(path)


def test_msa_tree_disabled_mode_is_valid_when_disabled(tmp_path: Path) -> None:
    import taxondbbuilder as builder

    path = tmp_path / "disabled.toml"
    _write_config(path, "msa_tree_mode = 'disabled'\n")

    assert builder.load_config(path)["post_prep"]["msa_tree_mode"] == "disabled"

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from .conftest import read_golden


def test_filter_terms_query_and_output_path(tmp_path: Path):
    import taxondbbuilder as builder

    filters = {
        "filter": ["mitochondrion"],
        "properties": "biomol_genomic",
        "sequence_length_min": 100,
        "sequence_length_max": 300,
        "publication_date_from": date(2020, 1, 2),
        "publication_date_to": "2024/12/31",
        "modification_date_from": datetime(2021, 2, 3),
        "all_fields_include": ["12S", "COI"],
        "all_fields_exclude": "WGS",
        "raw": "complete[prop]",
    }
    assert builder.build_filter_terms(filters) == [
        "mitochondrion[filter]",
        "biomol_genomic[prop]",
        "100[SLEN] : 300[SLEN]",
        "2020/01/02[PDAT] : 2024/12/31[PDAT]",
        "2021/02/03[MDAT] : 3000/12/31[MDAT]",
        '("12S"[All Fields] OR "COI"[All Fields])',
        'NOT ("WGS"[All Fields])',
        "complete[prop]",
    ]
    assert builder.build_query("9606", '"COI"[All Fields]', filters, True).startswith(
        '(txid9606[Organism:noexp]) AND ("COI"[All Fields]) AND (mitochondrion[filter])'
    )
    assert (
        builder.build_output_path(tmp_path / "nested" / "db.fasta", ["9606"], ["coi"])
        == tmp_path / "nested" / "db.fasta"
    )


def test_cli_list_markers_output_matches_golden(fixture_dir: Path, golden_dir: Path):
    import taxondbbuilder as builder

    result = CliRunner().invoke(
        builder.app, ["list-markers", "-c", "tests/fixtures/minimal_config.toml"]
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == read_golden(golden_dir, "cli-list-markers.txt")


def test_cli_list_primer_sets_output_matches_golden(
    fixture_dir: Path, golden_dir: Path
):
    import taxondbbuilder as builder

    result = CliRunner().invoke(
        builder.app, ["list-primer-sets", "-c", "tests/fixtures/minimal_config.toml"]
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == read_golden(golden_dir, "cli-list-primer-sets.txt")


@pytest.mark.parametrize("post_prep_cfg", [{}, {"msa_tree_enable": False}])
def test_resolve_post_prep_msa_tree_requires_enabled_config(
    tmp_path: Path, post_prep_cfg: dict[str, object]
) -> None:
    from taxondbbuilder.cli import _resolve_post_prep_options
    from taxondbbuilder.models import BuildSource, PostPrepStep

    with pytest.raises(
        typer.BadParameter,
        match="post-prep step 'msa_tree' requires post_prep.msa_tree_enable",
    ):
        _resolve_post_prep_options(
            True,
            [PostPrepStep.MSA_TREE],
            None,
            post_prep_cfg,
            tmp_path / "config.toml",
            BuildSource.NCBI,
        )

"""Direct tests for the FASTA record / source row pairing helper."""
from __future__ import annotations

from pathlib import Path

import pytest
from Bio.SeqRecord import SeqRecord

from taxondbbuilder.record_index import HeaderCollisionError, pair_fasta_with_rows


def _fasta(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "records.fasta"
    path.write_text(text, encoding="utf-8")
    return path


def test_emitted_side_collision_raises_before_reading_fasta(
    tmp_path: Path,
) -> None:
    fasta_path = _fasta(tmp_path, ">dup\nACGT\n")
    emitted = [{"header": "dup", "acc_id": "A"}, {"header": "dup", "acc_id": "B"}]
    with pytest.raises(HeaderCollisionError) as excinfo:
        pair_fasta_with_rows(fasta_path, emitted)
    message = str(excinfo.value)
    assert "'dup'" in message
    assert "2 source rows" in message


def test_fasta_side_collision_raises(tmp_path: Path) -> None:
    fasta_path = _fasta(tmp_path, ">dup\nACGT\n>dup\nTTTT\n")
    with pytest.raises(HeaderCollisionError) as excinfo:
        pair_fasta_with_rows(fasta_path, [{"header": "other", "acc_id": "A"}])
    assert "'dup'" in str(excinfo.value)


def test_unmapped_record_pairs_with_none(tmp_path: Path) -> None:
    fasta_path = _fasta(tmp_path, ">known\nACGT\n>unknown\nTTTT\n")
    row = {"header": "known", "acc_id": "A"}
    paired = pair_fasta_with_rows(fasta_path, [row])
    assert [source for _, source in paired] == [row, None]
    assert all(isinstance(record, SeqRecord) for record, _ in paired)
    assert [str(record.seq) for record, _ in paired] == ["ACGT", "TTTT"]


def test_error_message_names_the_offending_header(tmp_path: Path) -> None:
    header = "bold|BOLD_SAM001"
    emitted = [{"header": header, "acc_id": "A"}, {"header": header, "acc_id": "B"}]
    with pytest.raises(HeaderCollisionError) as excinfo:
        pair_fasta_with_rows(_fasta(tmp_path, ">x\nACGT\n"), emitted)
    assert header in str(excinfo.value)

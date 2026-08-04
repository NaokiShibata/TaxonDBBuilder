"""Post-prep sequence length filtering."""

from pathlib import Path
from typing import Dict, Optional

from Bio import SeqIO

def apply_post_prep_length_filter(
    fasta_path: Path,
    min_len: Optional[int],
    max_len: Optional[int],
) -> Dict[str, int]:
    before_count = 0
    after_count = 0
    tmp_path = fasta_path.with_suffix(fasta_path.suffix + ".postprep.tmp")
    try:
        with fasta_path.open("r", encoding="utf-8") as in_f, tmp_path.open("w", encoding="utf-8") as out_f:
            for record in SeqIO.parse(in_f, "fasta"):
                before_count += 1
                seq_len = len(record.seq)
                if min_len is not None and seq_len < min_len:
                    continue
                if max_len is not None and seq_len > max_len:
                    continue
                SeqIO.write(record, out_f, "fasta")
                after_count += 1
        tmp_path.replace(fasta_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return {
        "before": before_count,
        "after": after_count,
        "removed": before_count - after_count,
    }




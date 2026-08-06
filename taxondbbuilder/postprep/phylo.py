"""MSA and phylogenetic tree post-prep pipeline."""

from pathlib import Path

import kalign
import piqtree
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from cogent3 import make_aligned_seqs


def run_msa(
    records: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]] | None, str | None]:
    try:
        aligned_sequences = kalign.align(
            [sequence for _, sequence in records], seq_type="dna"
        )
    except Exception:
        return None, "msa_failed"
    return [
        (record_id, aligned_sequence)
        for (record_id, _), aligned_sequence in zip(records, aligned_sequences)
    ], None


def run_tree(
    aligned_records: list[tuple[str, str]], model: str
) -> tuple[str | None, str | None]:
    try:
        alignment = make_aligned_seqs(dict(aligned_records), moltype="dna")
        tree = piqtree.build_tree(alignment, model, rand_seed=1)
        return tree.get_newick(with_distances=True), None
    except Exception:
        return None, "tree_failed"


def apply_post_prep_msa_tree(fasta_path: Path, options: dict) -> dict:
    with fasta_path.open("r", encoding="utf-8") as in_f:
        records = [
            (record.id, str(record.seq)) for record in SeqIO.parse(in_f, "fasta")
        ]
    taxa_count = len(records)

    min_taxa = int(options.get("min_taxa", 3))
    max_samples = int(options.get("max_samples", 500))
    if taxa_count < min_taxa:
        return {
            "status": "skipped_too_few_taxa",
            "taxa_count": taxa_count,
            "msa_path": None,
            "tree_path": None,
        }
    if taxa_count > max_samples:
        return {
            "status": "skipped_too_many_taxa",
            "taxa_count": taxa_count,
            "msa_path": None,
            "tree_path": None,
        }

    msa_path = fasta_path.with_suffix(fasta_path.suffix + ".msa.fasta")
    tree_path = fasta_path.with_suffix(fasta_path.suffix + ".tree.nwk")
    aligned_records, error = run_msa(records)
    if error or aligned_records is None:
        return {
            "status": error or "msa_failed",
            "taxa_count": taxa_count,
            "msa_path": None,
            "tree_path": None,
        }

    with msa_path.open("w", encoding="utf-8") as out_f:
        SeqIO.write(
            (
                SeqRecord(Seq(sequence), id=record_id, description="")
                for record_id, sequence in aligned_records
            ),
            out_f,
            "fasta",
        )

    newick_text, error = run_tree(
        aligned_records, str(options.get("model", "GTR+G"))
    )
    if error or newick_text is None:
        return {
            "status": error or "tree_failed",
            "taxa_count": taxa_count,
            "msa_path": str(msa_path),
            "tree_path": None,
        }
    tree_path.write_text(newick_text, encoding="utf-8")

    return {
        "status": "ok",
        "taxa_count": taxa_count,
        "msa_path": str(msa_path),
        "tree_path": str(tree_path),
    }

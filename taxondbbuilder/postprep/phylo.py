"""MSA and phylogenetic tree post-prep pipeline."""

from pathlib import Path
from typing import Any

import kalign
import piqtree
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from cogent3 import make_aligned_seqs


def _ensure_unique_record_ids(
    records: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    used: set[str] = set()
    next_suffix: dict[str, int] = {}
    unique_records: list[tuple[str, str]] = []

    for raw_id, sequence in records:
        base_id = raw_id or "sequence"
        record_id = base_id
        suffix = next_suffix.get(base_id, 2)
        while record_id in used:
            record_id = f"{base_id}__{suffix}"
            suffix += 1
        next_suffix[base_id] = suffix
        used.add(record_id)
        unique_records.append((record_id, sequence))

    return unique_records


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
    aligned_records: list[tuple[str, str]],
    model: str,
    bootstrap_replicates: int = 1000,
) -> tuple[str | None, str | None]:
    try:
        unique_records = _ensure_unique_record_ids(aligned_records)
        alignment = make_aligned_seqs(dict(unique_records), moltype="dna")
        tree = piqtree.build_tree(
            alignment,
            model,
            rand_seed=1,
            bootstrap_replicates=bootstrap_replicates,
        )
        # piqtree stores ultrafast-bootstrap support on internal nodes.  Copy
        # it to the Newick internal label so every downstream Newick reader,
        # including the GUI, receives the support value explicitly.
        for node in tree.postorder():
            if node.is_tip() or node.is_root():
                continue
            support = getattr(node, "support", None)
            if support is not None:
                node.name = f"{float(support):g}"
        return tree.get_newick(with_distances=True), None
    except Exception:
        return None, "tree_failed"


def _write_msa(path: Path, aligned_records: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as out_f:
        SeqIO.write(
            (
                SeqRecord(Seq(sequence), id=record_id, description="")
                for record_id, sequence in aligned_records
            ),
            out_f,
            "fasta",
        )


def _run_single_tree(
    records: list[tuple[str, str]],
    fasta_path: Path,
    options: dict[str, Any],
    output_suffix: str = "",
) -> dict[str, Any]:
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

    stem = f"{fasta_path.name}{output_suffix}"
    msa_path = fasta_path.with_name(f"{stem}.msa.fasta")
    tree_path = fasta_path.with_name(f"{stem}.tree.nwk")
    aligned_records, error = run_msa(records)
    if error or aligned_records is None:
        return {
            "status": error or "msa_failed",
            "taxa_count": taxa_count,
            "msa_path": None,
            "tree_path": None,
        }

    aligned_records = _ensure_unique_record_ids(aligned_records)
    _write_msa(msa_path, aligned_records)

    newick_text, error = run_tree(
        aligned_records,
        str(options.get("model", "GTR+G")),
        int(options.get("bootstrap_replicates", 1000)),
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


def _read_fasta_records(fasta_path: Path) -> list[tuple[str, str, str]]:
    with fasta_path.open("r", encoding="utf-8") as in_f:
        return [
            (record.id, record.description, str(record.seq))
            for record in SeqIO.parse(in_f, "fasta")
        ]


def apply_post_prep_msa_tree(
    fasta_path: Path,
    options: dict,
    taxid_by_header: dict[str, str] | None = None,
) -> dict:
    fasta_records = _read_fasta_records(fasta_path)
    records = [(record_id, sequence) for record_id, _, sequence in fasta_records]
    mode = str(options.get("mode", "combined"))

    if mode == "per_taxid":
        if taxid_by_header is None:
            return {
                "status": "taxid_metadata_missing",
                "mode": mode,
                "taxa_count": len(records),
                "tree_outputs": [],
            }

        grouped: dict[str, list[tuple[str, str]]] = {}
        for record_id, description, sequence in fasta_records:
            taxid = taxid_by_header.get(description) or taxid_by_header.get(record_id)
            if not taxid:
                return {
                    "status": "taxid_metadata_missing",
                    "mode": mode,
                    "taxa_count": len(records),
                    "tree_outputs": [],
                }
            grouped.setdefault(taxid, []).append((record_id, sequence))

        outputs = []
        for taxid, taxid_records in sorted(grouped.items()):
            result = _run_single_tree(
                taxid_records, fasta_path, options, output_suffix=f".taxid{taxid}"
            )
            outputs.append({"taxid": taxid, **result})

        status = "ok" if all(item["status"] == "ok" for item in outputs) else "partial_failed"
        return {
            "status": status,
            "mode": mode,
            "taxa_count": len(records),
            "tree_outputs": outputs,
        }

    return _run_single_tree(records, fasta_path, options)

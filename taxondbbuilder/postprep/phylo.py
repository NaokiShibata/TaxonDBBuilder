"""MSA and phylogenetic tree post-prep pipeline."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from Bio import SeqIO


def run_mafft(fasta_path: Path, out_path: Path) -> str | None:
    mafft_bin = shutil.which("mafft")
    if not mafft_bin:
        return "mafft_not_found"
    cmd = [mafft_bin, "--auto", str(fasta_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return "mafft_failed"
    if proc.returncode != 0:
        return "mafft_failed"
    try:
        out_path.write_text(proc.stdout, encoding="utf-8")
    except OSError:
        return "mafft_failed"
    return None


def run_iqtree(
    msa_path: Path, out_dir: Path, model: str
) -> tuple[Path | None, str | None]:
    iqtree_bin = shutil.which("iqtree2") or shutil.which("iqtree")
    if not iqtree_bin:
        return None, "iqtree_not_found"
    prefix = out_dir / "iqtree"
    cmd = [
        iqtree_bin,
        "-s",
        str(msa_path),
        "-m",
        model,
        "-fast",
        "-pre",
        str(prefix),
        "-redo",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return None, "iqtree_failed"
    treefile = Path(f"{prefix}.treefile")
    if proc.returncode != 0 or not treefile.exists():
        return None, "iqtree_failed"
    return treefile, None


def apply_post_prep_msa_tree(fasta_path: Path, options: dict) -> dict:
    with fasta_path.open("r", encoding="utf-8") as in_f:
        taxa_count = sum(1 for _ in SeqIO.parse(in_f, "fasta"))

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
    with tempfile.TemporaryDirectory(prefix="taxondb-msa-tree-") as tmpdir:
        tmp_path = Path(tmpdir)
        tmp_msa_path = tmp_path / "alignment.fasta"
        error = run_mafft(fasta_path, tmp_msa_path)
        if error:
            return {
                "status": error,
                "taxa_count": taxa_count,
                "msa_path": None,
                "tree_path": None,
            }
        shutil.copyfile(tmp_msa_path, msa_path)

        treefile, error = run_iqtree(
            tmp_msa_path, tmp_path, str(options.get("model", "GTR+G"))
        )
        if error or treefile is None:
            return {
                "status": error or "iqtree_failed",
                "taxa_count": taxa_count,
                "msa_path": str(msa_path),
                "tree_path": None,
            }
        shutil.copyfile(treefile, tree_path)

    return {
        "status": "ok",
        "taxa_count": taxa_count,
        "msa_path": str(msa_path),
        "tree_path": str(tree_path),
    }

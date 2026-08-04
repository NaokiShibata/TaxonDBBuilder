"""Primer trimming post-prep pipeline."""

import csv
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Bio import SeqIO
from Bio.Seq import Seq

from ..models import (
    IUPAC_DNA_VALUES,
    PRIMER_TRIM_MODE_BOTH_REQUIRED,
    PRIMER_TRIM_MODE_ONE_OR_BOTH,
)

@dataclass
class PrimerEndpointMatch:
    primer: str
    overlap_bp: int
    mismatches: int
    score: int
    full_len_match: bool
    trim_bp: int


@dataclass
class OrientationScore:
    name: str
    left: Optional[PrimerEndpointMatch]
    right: Optional[PrimerEndpointMatch]

    @property
    def matched_ends(self) -> int:
        return (1 if self.left else 0) + (1 if self.right else 0)

    @property
    def score_total(self) -> int:
        left_score = self.left.score if self.left else 0
        right_score = self.right.score if self.right else 0
        return left_score + right_score

    @property
    def mismatch_total(self) -> int:
        left_mm = self.left.mismatches if self.left else 0
        right_mm = self.right.mismatches if self.right else 0
        return left_mm + right_mm

    def rank(self) -> Tuple[int, int, int]:
        return (self.matched_ends, self.score_total, -self.mismatch_total)


def primer_base_matches(seq_base: str, primer_base: str) -> bool:
    values = IUPAC_DNA_VALUES.get(primer_base.upper())
    if values is None:
        return False
    return seq_base.upper() in values


def required_overlap_bp(primer_len: int, min_overlap_bp: Optional[int], min_overlap_ratio: float) -> int:
    ratio_required = int(round(primer_len * min_overlap_ratio))
    ratio_required = max(1, min(primer_len, ratio_required))
    if min_overlap_bp is None:
        return ratio_required
    return max(1, min(primer_len, max(min_overlap_bp, ratio_required)))


def count_mismatches(seq_segment: str, primer_segment: str) -> int:
    return sum(1 for s, p in zip(seq_segment.upper(), primer_segment.upper()) if not primer_base_matches(s, p))


def find_best_prefix_match(
    seq: str,
    primers: List[str],
    max_mismatch: int,
    max_error_rate: float,
    min_overlap_bp: Optional[int],
    min_overlap_ratio: float,
    max_offset: int = 0,
) -> Optional[PrimerEndpointMatch]:
    best: Optional[PrimerEndpointMatch] = None
    for primer in primers:
        max_overlap = min(len(primer), len(seq))
        required = required_overlap_bp(len(primer), min_overlap_bp, min_overlap_ratio)
        if max_overlap < required:
            continue
        max_shift = min(max_offset, max(0, len(seq) - required))
        for offset in range(max_shift + 1):
            available = len(seq) - offset
            if available < required:
                continue
            for overlap in range(min(max_overlap, available), required - 1, -1):
                primer_seg = primer[-overlap:]
                seq_seg = seq[offset : offset + overlap]
                mismatches = count_mismatches(seq_seg, primer_seg)
                if mismatches > max_mismatch:
                    continue
                error_rate = mismatches / overlap if overlap else 1.0
                if error_rate > max_error_rate:
                    continue
                candidate = PrimerEndpointMatch(
                    primer=primer,
                    overlap_bp=overlap,
                    mismatches=mismatches,
                    score=overlap - mismatches,
                    full_len_match=(overlap == len(primer)),
                    trim_bp=offset + overlap,
                )
                if best is None:
                    best = candidate
                    continue
                current_key = (
                    candidate.overlap_bp,
                    candidate.score,
                    -candidate.mismatches,
                    int(candidate.full_len_match),
                    -offset,
                )
                best_key = (
                    best.overlap_bp,
                    best.score,
                    -best.mismatches,
                    int(best.full_len_match),
                    -(best.trim_bp - best.overlap_bp),
                )
                if current_key > best_key:
                    best = candidate
    return best


def find_best_suffix_match(
    seq: str,
    primers: List[str],
    max_mismatch: int,
    max_error_rate: float,
    min_overlap_bp: Optional[int],
    min_overlap_ratio: float,
    max_offset: int = 0,
) -> Optional[PrimerEndpointMatch]:
    best: Optional[PrimerEndpointMatch] = None
    for primer in primers:
        max_overlap = min(len(primer), len(seq))
        required = required_overlap_bp(len(primer), min_overlap_bp, min_overlap_ratio)
        if max_overlap < required:
            continue
        max_shift = min(max_offset, max(0, len(seq) - required))
        for shift in range(max_shift + 1):
            end = len(seq) - shift
            if end < required:
                continue
            for overlap in range(min(max_overlap, end), required - 1, -1):
                start = end - overlap
                primer_seg = primer[:overlap]
                seq_seg = seq[start:end]
                mismatches = count_mismatches(seq_seg, primer_seg)
                if mismatches > max_mismatch:
                    continue
                error_rate = mismatches / overlap if overlap else 1.0
                if error_rate > max_error_rate:
                    continue
                candidate = PrimerEndpointMatch(
                    primer=primer,
                    overlap_bp=overlap,
                    mismatches=mismatches,
                    score=overlap - mismatches,
                    full_len_match=(overlap == len(primer)),
                    trim_bp=len(seq) - start,
                )
                if best is None:
                    best = candidate
                    continue
                current_key = (
                    candidate.overlap_bp,
                    candidate.score,
                    -candidate.mismatches,
                    int(candidate.full_len_match),
                    -shift,
                )
                best_shift = max(0, best.trim_bp - best.overlap_bp)
                best_key = (
                    best.overlap_bp,
                    best.score,
                    -best.mismatches,
                    int(best.full_len_match),
                    -best_shift,
                )
                if current_key > best_key:
                    best = candidate
    return best


def resolve_orientation(seq: str, canonical: OrientationScore, reverse: OrientationScore) -> Tuple[OrientationScore, bool]:
    can_rank = canonical.rank()
    rev_rank = reverse.rank()
    if can_rank > rev_rank:
        return canonical, False
    if rev_rank > can_rank:
        return reverse, False
    return canonical, True


def confidence_label(matched_ends: int, mismatch_total: int, ambiguous_orientation: bool) -> str:
    if matched_ends == 2 and mismatch_total <= 1 and not ambiguous_orientation:
        return "high"
    if matched_ends >= 1:
        return "medium"
    return "low"


def compute_trim_lengths_from_row(row: Dict[str, Any], trim_mode: str) -> Tuple[int, int]:
    left_hit = int(row.get("left_hit", 0)) > 0
    right_hit = int(row.get("right_hit", 0)) > 0
    left_trim_bp = int(row.get("left_trim_bp", 0) or 0)
    right_trim_bp = int(row.get("right_trim_bp", 0) or 0)
    left_overlap_bp = int(row.get("left_overlap_bp", 0) or 0)
    right_overlap_bp = int(row.get("right_overlap_bp", 0) or 0)
    left_overlap = left_trim_bp if left_trim_bp > 0 else left_overlap_bp
    right_overlap = right_trim_bp if right_trim_bp > 0 else right_overlap_bp
    if trim_mode == PRIMER_TRIM_MODE_ONE_OR_BOTH:
        return (left_overlap if left_hit else 0, right_overlap if right_hit else 0)
    if trim_mode == PRIMER_TRIM_MODE_BOTH_REQUIRED:
        if left_hit and right_hit:
            return left_overlap, right_overlap
        return 0, 0
    return 0, 0


def _summarize_trim_row(
    row: Dict[str, Any], seq: str, trim_mode: str
) -> Tuple[Optional[Tuple[str, str]], Dict[str, int]]:
    confidence = str(row.get("confidence", "low"))
    counts = {
        "confidence_high": int(confidence == "high"),
        "confidence_medium": int(confidence == "medium"),
        "confidence_low": int(confidence not in {"high", "medium"}),
        "canonical_orientation": 0,
        "reverse_orientation": 0,
        "trimmed_both": 0,
        "trimmed_left_only": 0,
        "trimmed_right_only": 0,
        "untrimmed": 0,
        "dropped_empty": 0,
    }
    matched_ends = int(row.get("left_hit", 0)) + int(row.get("right_hit", 0))
    if matched_ends > 0:
        orientation = "canonical" if str(row.get("orientation_chosen")) == "canonical" else "reverse"
        counts[f"{orientation}_orientation"] += 1

    left_len, right_len = compute_trim_lengths_from_row(row, trim_mode)
    ends_trimmed = (1 if left_len else 0) + (1 if right_len else 0)
    if ends_trimmed == 0:
        counts["untrimmed"] += 1
    elif ends_trimmed == 2:
        counts["trimmed_both"] += 1
    elif left_len:
        counts["trimmed_left_only"] += 1
    else:
        counts["trimmed_right_only"] += 1

    right_index = len(seq) - right_len if right_len else len(seq)
    trimmed_seq = seq[left_len:right_index]
    row["trim_start"] = left_len
    row["trim_end"] = right_index
    if not trimmed_seq:
        counts["dropped_empty"] += 1
        row["dropped_empty"] = 1
        return None, counts
    row["dropped_empty"] = 0
    return (str(row.get("record_id", "")), trimmed_seq), counts


def summarize_rows_to_records(
    rows: List[Dict[str, Any]],
    seq_by_header: Dict[str, str],
    trim_mode: str,
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    trimmed_records: List[Tuple[str, str]] = []
    counts = {key: 0 for key in (
        "trimmed_both", "trimmed_left_only", "trimmed_right_only", "untrimmed",
        "dropped_empty", "canonical_orientation", "reverse_orientation",
        "confidence_high", "confidence_medium", "confidence_low",
    )}
    for row in rows:
        seq = seq_by_header.get(str(row.get("record_id", "")), "")
        if not seq:
            continue
        trimmed_record, row_counts = _summarize_trim_row(row, seq, trim_mode)
        for key, value in row_counts.items():
            counts[key] += value
        if trimmed_record is not None:
            trimmed_records.append(trimmed_record)

    before = len(rows)
    return trimmed_records, {
        "before": before,
        "after": len(trimmed_records),
        "removed": before - len(trimmed_records),
        **counts,
        "high_conf_rate": counts["confidence_high"] / before if before else 0.0,
    }


def _collect_vsearch_candidates(
    rows: List[Dict[str, Any]], seq_by_header: Dict[str, str]
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if str(row.get("confidence", "")) not in {"low", "medium"}:
            row["recheck_status"] = "not_target_confidence"
            continue
        if int(row.get("left_hit", 0)) and int(row.get("right_hit", 0)):
            row["recheck_status"] = "already_both_ends"
            continue
        header = str(row.get("record_id", ""))
        seq = seq_by_header.get(header, "")
        if not seq:
            row["recheck_status"] = "sequence_not_found"
            continue
        candidates.append({"idx": idx, "header": header, "seq": seq, "row": row})
    return candidates


def _build_vsearch_primer_db(
    forward_primers: List[str],
    reverse_primers: List[str],
    forward_rc: List[str],
    reverse_rc: List[str],
) -> List[Tuple[str, str]]:
    entries: List[Tuple[str, str]] = []
    for i, primer in enumerate(forward_primers):
        entries.append((f"CL_{i}", primer))
    for i, primer in enumerate(reverse_rc):
        entries.append((f"CR_{i}", primer))
    for i, primer in enumerate(reverse_primers):
        entries.append((f"RL_{i}", primer))
    for i, primer in enumerate(forward_rc):
        entries.append((f"RR_{i}", primer))
    return entries


def _prepare_vsearch_inputs(
    tmp: Path,
    candidates: List[Dict[str, Any]],
    primer_db_entries: List[Tuple[str, str]],
) -> Tuple[Path, Path, Path, List[Tuple[int, str, str]], int]:
    db_fa = tmp / "primers.fa"
    q_fa = tmp / "queries.fa"
    out_path = tmp / "hits.tsv"
    with db_fa.open("w", encoding="utf-8") as f:
        for pid, seq in primer_db_entries:
            f.write(f">{pid}\n{seq}\n")

    max_primer_len = max((len(p) for _, p in primer_db_entries), default=30)
    window_len = max(20, max_primer_len + 8)
    query_rows: List[Tuple[int, str, str]] = []
    attempted = 0
    with q_fa.open("w", encoding="utf-8") as f:
        for candidate in candidates:
            row = candidate["row"]
            seq = candidate["seq"]
            if not int(row.get("left_hit", 0)):
                qid = f"{candidate['idx']}|L"
                f.write(f">{qid}\n{seq[:window_len]}\n")
                query_rows.append((candidate["idx"], "L", qid))
                attempted += 1
            if not int(row.get("right_hit", 0)):
                qid = f"{candidate['idx']}|R"
                f.write(f">{qid}\n{seq[-window_len:]}\n")
                query_rows.append((candidate["idx"], "R", qid))
                attempted += 1
    return db_fa, q_fa, out_path, query_rows, attempted


def _run_vsearch_command(
    vsearch_bin: str,
    q_fa: Path,
    db_fa: Path,
    out_path: Path,
    min_identity: float,
) -> Optional[str]:
    cmd = [
        vsearch_bin,
        "--usearch_global",
        str(q_fa),
        "--db",
        str(db_fa),
        "--id",
        f"{min_identity:.4f}",
        "--strand",
        "plus",
        "--blast6out",
        str(out_path),
        "--maxaccepts",
        "16",
        "--maxrejects",
        "64",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return "vsearch_failed"
    if not out_path.exists():
        return "no_output"
    return None


def _parse_vsearch_hits(
    out_path: Path,
    primer_db_entries: List[Tuple[str, str]],
    min_identity: float,
    min_query_cov: float,
) -> Dict[str, Dict[str, Any]]:
    primer_len_map = {pid: len(seq) for pid, seq in primer_db_entries}
    best_hits: Dict[str, Dict[str, Any]] = {}
    with out_path.open("r", encoding="utf-8") as f:
        for line in f:
            cols = line.strip().split("\t")
            if len(cols) < 12:
                continue
            qid, sid = cols[0], cols[1]
            try:
                pident = float(cols[2]) / 100.0
                aln_len = int(cols[3])
                mismatch = int(cols[4])
            except ValueError:
                continue
            primer_len = primer_len_map.get(sid, 0)
            if primer_len <= 0:
                continue
            cov = aln_len / primer_len
            if pident < min_identity or cov < min_query_cov:
                continue
            hit = {"sid": sid, "pident": pident, "aln_len": aln_len, "mismatch": mismatch, "cov": cov}
            previous = best_hits.get(qid)
            if previous is None:
                best_hits[qid] = hit
                continue
            current_key = (hit["pident"], hit["cov"], hit["aln_len"], -hit["mismatch"])
            previous_key = (previous["pident"], previous["cov"], previous["aln_len"], -previous["mismatch"])
            if current_key > previous_key:
                best_hits[qid] = hit
    return best_hits


def _merge_vsearch_hits(
    rows: List[Dict[str, Any]],
    query_rows: List[Tuple[int, str, str]],
    best_hits: Dict[str, Dict[str, Any]],
) -> int:
    rescued = 0
    hit_applied: Dict[Tuple[int, str], bool] = {}
    for idx, side, qid in query_rows:
        hit = best_hits.get(qid)
        row = rows[idx]
        if hit is None:
            continue
        sid = hit["sid"]
        is_left_subject = sid.startswith("CL_") or sid.startswith("RL_")
        is_right_subject = sid.startswith("CR_") or sid.startswith("RR_")
        if side == "L" and not is_left_subject:
            continue
        if side == "R" and not is_right_subject:
            continue
        row["recheck_status"] = "rescued_by_vsearch"
        prefix = "left" if side == "L" else "right"
        row[f"{prefix}_hit"] = 1
        row[f"{prefix}_overlap_bp"] = int(hit["aln_len"])
        row[f"{prefix}_trim_bp"] = int(hit["aln_len"])
        row[f"{prefix}_mismatch"] = int(hit["mismatch"])
        mism_total = int(row.get("left_mismatch", 0)) + int(row.get("right_mismatch", 0))
        matched_ends = int(row.get("left_hit", 0)) + int(row.get("right_hit", 0))
        row["confidence"] = confidence_label(matched_ends, mism_total, bool(int(row.get("orientation_ambiguous", 0))))
        rescued += 1
        hit_applied[(idx, side)] = True
    for idx, side, _ in query_rows:
        if hit_applied.get((idx, side)):
            continue
        row = rows[idx]
        if str(row.get("recheck_status", "")) != "rescued_by_vsearch":
            row["recheck_status"] = "attempted_no_hit"
    return rescued


def run_vsearch_endpoint_recheck(
    rows: List[Dict[str, Any]],
    seq_by_header: Dict[str, str],
    forward_primers: List[str],
    reverse_primers: List[str],
    forward_rc: List[str],
    reverse_rc: List[str],
    min_identity: float,
    min_query_cov: float,
) -> Tuple[int, int, Optional[str]]:
    vsearch_bin = shutil.which("vsearch")
    if not vsearch_bin:
        return 0, 0, "vsearch_not_found"
    candidates = _collect_vsearch_candidates(rows, seq_by_header)
    if not candidates:
        return 0, 0, None
    primer_db_entries = _build_vsearch_primer_db(forward_primers, reverse_primers, forward_rc, reverse_rc)
    with tempfile.TemporaryDirectory(prefix="taxondb-vsearch-") as tmpdir:
        db_fa, q_fa, out_path, query_rows, attempted = _prepare_vsearch_inputs(
            Path(tmpdir), candidates, primer_db_entries
        )
        error = _run_vsearch_command(vsearch_bin, q_fa, db_fa, out_path, min_identity)
        if error == "vsearch_failed":
            return attempted, 0, error
        if error == "no_output":
            return attempted, 0, None
        best_hits = _parse_vsearch_hits(out_path, primer_db_entries, min_identity, min_query_cov)
        rescued = _merge_vsearch_hits(rows, query_rows, best_hits)
    return attempted, rescued, None


def _read_primer_records(fasta_path: Path) -> List[Tuple[str, str]]:
    records: List[Tuple[str, str]] = []
    with fasta_path.open("r", encoding="utf-8") as in_f:
        for record in SeqIO.parse(in_f, "fasta"):
            records.append((record.description, str(record.seq).upper().replace("U", "T")))
    return records


@dataclass
class _PrimerTrimContext:
    fasta_path: Path
    forward_primers: List[str]
    reverse_primers: List[str]
    forward_rc: List[str]
    reverse_rc: List[str]
    trim_mode: str
    max_mismatch: int
    max_error_rate: float
    min_overlap_bp: Optional[int]
    min_overlap_ratio: float
    end_max_offset: int
    keep_retained_fasta: bool
    iter_enable: bool
    iter_max_rounds: int
    iter_stop_delta: float
    iter_target_conf: float
    sidecar_format: str
    recheck_tool: str
    recheck_min_identity: float
    recheck_min_query_cov: float
    phylo_target_confidence: str
    retained_path: Path


@dataclass
class _PrimerRoundResult:
    records: List[Tuple[str, str]]
    rows: List[Dict[str, Any]]
    summary: Dict[str, Any]


def _write_primer_outputs(
    fasta_path: Path,
    best_records: List[Tuple[str, str]],
    sidecar_rows: List[Dict[str, Any]],
    sidecar_format: str,
) -> Optional[Path]:
    tmp_path = fasta_path.with_suffix(fasta_path.suffix + ".postprep.primer.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as out_f:
            for header, seq in best_records:
                out_f.write(f">{header}\n")
                out_f.write(f"{seq}\n")
        tmp_path.replace(fasta_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    sidecar_path = None
    if sidecar_format == "jsonl":
        sidecar_path = fasta_path.with_suffix(fasta_path.suffix + ".postprep.primer.jsonl")
        with sidecar_path.open("w", encoding="utf-8") as f:
            for row in sidecar_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    else:
        sidecar_path = fasta_path.with_suffix(fasta_path.suffix + ".postprep.primer.tsv")
        fields = [
            "record_id",
            "round",
            "orientation_chosen",
            "orientation_ambiguous",
            "left_hit",
            "right_hit",
            "left_overlap_bp",
            "right_overlap_bp",
            "left_trim_bp",
            "right_trim_bp",
            "left_mismatch",
            "right_mismatch",
            "left_primer_name",
            "right_primer_name",
            "trim_start",
            "trim_end",
            "trim_mode",
            "confidence",
            "dropped_empty",
            "recheck_tool",
            "recheck_status",
            "phylo_mismatch_flag",
        ]
        with sidecar_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(sidecar_rows)
    return sidecar_path


def _build_primer_trim_context(
    fasta_path: Path,
    forward_primers: List[str],
    reverse_primers: List[str],
    options: Optional[Dict[str, Any]],
) -> _PrimerTrimContext:
    opts = options or {}
    min_overlap_bp = opts.get("min_overlap_bp")
    if min_overlap_bp is not None:
        min_overlap_bp = int(min_overlap_bp)
    return _PrimerTrimContext(
        fasta_path=fasta_path,
        forward_primers=forward_primers,
        reverse_primers=reverse_primers,
        forward_rc=[str(Seq(p).reverse_complement()).upper() for p in forward_primers],
        reverse_rc=[str(Seq(p).reverse_complement()).upper() for p in reverse_primers],
        trim_mode=str(opts.get("trim_mode", PRIMER_TRIM_MODE_ONE_OR_BOTH)).strip().lower(),
        max_mismatch=int(opts.get("max_mismatch", 0)),
        max_error_rate=float(opts.get("max_error_rate", 0.0)),
        min_overlap_bp=min_overlap_bp,
        min_overlap_ratio=float(opts.get("min_overlap_ratio", 1.0)),
        end_max_offset=int(opts.get("end_max_offset", 0)),
        keep_retained_fasta=bool(opts.get("keep_retained_fasta", True)),
        iter_enable=bool(opts.get("iter_enable", False)),
        iter_max_rounds=int(opts.get("iter_max_rounds", 3)),
        iter_stop_delta=float(opts.get("iter_stop_delta", 0.002)),
        iter_target_conf=float(opts.get("iter_target_conf", 0.98)),
        sidecar_format=str(opts.get("sidecar_format", "tsv")).strip().lower(),
        recheck_tool=str(opts.get("recheck_tool", "off")).strip().lower(),
        recheck_min_identity=float(opts.get("recheck_min_identity", 0.85)),
        recheck_min_query_cov=float(opts.get("recheck_min_query_cov", 0.7)),
        phylo_target_confidence=str(opts.get("phylo_target_confidence", "medium")).strip().lower(),
        retained_path=fasta_path.with_suffix(fasta_path.suffix + ".postprep.primer.retained.fasta"),
    )


def _prepare_primer_trim(ctx: _PrimerTrimContext) -> List[Tuple[str, str]]:
    if ctx.keep_retained_fasta:
        shutil.copyfile(ctx.fasta_path, ctx.retained_path)
    return _read_primer_records(ctx.fasta_path)


def _empty_primer_trim_result(ctx: _PrimerTrimContext) -> Dict[str, Any]:
    return {
        "before": 0,
        "after": 0,
        "removed": 0,
        "trimmed_both": 0,
        "trimmed_left_only": 0,
        "trimmed_right_only": 0,
        "untrimmed": 0,
        "dropped_empty": 0,
        "canonical_orientation": 0,
        "reverse_orientation": 0,
        "confidence_high": 0,
        "confidence_medium": 0,
        "confidence_low": 0,
        "rounds_run": 0,
        "best_round": 0,
        "high_conf_rate": 0.0,
        "sidecar_path": None,
        "retained_path": str(ctx.retained_path) if ctx.keep_retained_fasta else None,
        "recheck_tool": ctx.recheck_tool,
        "phylo_target_confidence": ctx.phylo_target_confidence,
    }


def _round_thresholds(
    ctx: _PrimerTrimContext, round_idx: int
) -> Tuple[int, float, Optional[int]]:
    relax = round_idx - 1
    overlap_bp = ctx.min_overlap_bp
    if overlap_bp is not None:
        overlap_bp = max(8, overlap_bp - relax)
    return ctx.max_mismatch + relax, max(0.5, ctx.min_overlap_ratio - (0.05 * relax)), overlap_bp


def _find_orientation_matches(
    ctx: _PrimerTrimContext,
    seq: str,
    max_mismatch: int,
    overlap_ratio: float,
    overlap_bp: Optional[int],
) -> Tuple[OrientationScore, bool]:
    canonical = OrientationScore(
        name="canonical",
        left=find_best_prefix_match(
            seq, ctx.forward_primers, max_mismatch, ctx.max_error_rate,
            overlap_bp, overlap_ratio, max_offset=ctx.end_max_offset,
        ),
        right=find_best_suffix_match(
            seq, ctx.reverse_rc, max_mismatch, ctx.max_error_rate,
            overlap_bp, overlap_ratio, max_offset=ctx.end_max_offset,
        ),
    )
    reverse = OrientationScore(
        name="reverse",
        left=find_best_prefix_match(
            seq, ctx.reverse_primers, max_mismatch, ctx.max_error_rate,
            overlap_bp, overlap_ratio, max_offset=ctx.end_max_offset,
        ),
        right=find_best_suffix_match(
            seq, ctx.forward_rc, max_mismatch, ctx.max_error_rate,
            overlap_bp, overlap_ratio, max_offset=ctx.end_max_offset,
        ),
    )
    return resolve_orientation(seq, canonical, reverse)


def _build_primer_row(
    ctx: _PrimerTrimContext,
    header: str,
    round_idx: int,
    chosen: OrientationScore,
    ambiguous_orientation: bool,
    left_len: int,
    right_len: int,
    right_index: int,
    confidence: str,
    dropped_empty: int,
) -> Dict[str, Any]:
    return {
        "record_id": header,
        "round": round_idx,
        "orientation_chosen": chosen.name,
        "orientation_ambiguous": int(ambiguous_orientation),
        "left_hit": int(chosen.left is not None),
        "right_hit": int(chosen.right is not None),
        "left_overlap_bp": chosen.left.overlap_bp if chosen.left else 0,
        "right_overlap_bp": chosen.right.overlap_bp if chosen.right else 0,
        "left_trim_bp": left_len,
        "right_trim_bp": right_len,
        "left_mismatch": chosen.left.mismatches if chosen.left else 0,
        "right_mismatch": chosen.right.mismatches if chosen.right else 0,
        "left_primer_name": chosen.left.primer if chosen.left else "",
        "right_primer_name": chosen.right.primer if chosen.right else "",
        "trim_start": left_len,
        "trim_end": right_index,
        "trim_mode": ctx.trim_mode,
        "confidence": confidence,
        "dropped_empty": dropped_empty,
        "recheck_tool": ctx.recheck_tool,
        "recheck_status": "not_run_phase2",
        "phylo_mismatch_flag": 0,
    }


def _trim_record_for_round(
    ctx: _PrimerTrimContext,
    header: str,
    seq: str,
    round_idx: int,
    max_mismatch: int,
    overlap_ratio: float,
    overlap_bp: Optional[int],
) -> Tuple[Dict[str, Any], Optional[str], int, int, int, int, int, int, int, int, int]:
    chosen, ambiguous_orientation = _find_orientation_matches(
        ctx, seq, max_mismatch, overlap_ratio, overlap_bp
    )
    confidence = confidence_label(chosen.matched_ends, chosen.mismatch_total, ambiguous_orientation)
    do_left_trim = ctx.trim_mode == PRIMER_TRIM_MODE_ONE_OR_BOTH and chosen.left is not None
    do_right_trim = ctx.trim_mode == PRIMER_TRIM_MODE_ONE_OR_BOTH and chosen.right is not None
    if ctx.trim_mode == PRIMER_TRIM_MODE_BOTH_REQUIRED:
        do_left_trim = do_right_trim = chosen.left is not None and chosen.right is not None
    left_len = chosen.left.trim_bp if do_left_trim and chosen.left else 0
    right_len = chosen.right.trim_bp if do_right_trim and chosen.right else 0
    right_index = len(seq) - right_len if right_len else len(seq)
    trimmed_seq = seq[left_len:right_index]
    row = _build_primer_row(
        ctx, header, round_idx, chosen, ambiguous_orientation, left_len, right_len, right_index,
        confidence, int(not trimmed_seq),
    )
    ends_trimmed = (1 if left_len else 0) + (1 if right_len else 0)
    return (
        row, trimmed_seq or None, ends_trimmed, int(chosen.name == "canonical" and chosen.matched_ends > 0),
        int(chosen.name == "reverse" and chosen.matched_ends > 0), int(confidence == "high"),
        int(confidence == "medium"), int(confidence == "low"), int(not trimmed_seq), left_len, right_len,
    )


def _run_primer_trim_round(
    ctx: _PrimerTrimContext, records: List[Tuple[str, str]], round_idx: int
) -> _PrimerRoundResult:
    max_mismatch, overlap_ratio, overlap_bp = _round_thresholds(ctx, round_idx)
    trimmed_records: List[Tuple[str, str]] = []
    rows: List[Dict[str, Any]] = []
    counts = {"trimmed_both": 0, "trimmed_left_only": 0, "trimmed_right_only": 0,
              "untrimmed": 0, "dropped_empty": 0, "canonical_orientation": 0,
              "reverse_orientation": 0, "confidence_high": 0, "confidence_medium": 0,
              "confidence_low": 0}
    for header, seq in records:
        row, trimmed_seq, ends_trimmed, canonical, reverse, high, medium, low, dropped, _, _ = (
            _trim_record_for_round(ctx, header, seq, round_idx, max_mismatch, overlap_ratio, overlap_bp)
        )
        rows.append(row)
        if ends_trimmed == 2:
            counts["trimmed_both"] += 1
        elif ends_trimmed == 1 and row["left_trim_bp"]:
            counts["trimmed_left_only"] += 1
        elif ends_trimmed == 1:
            counts["trimmed_right_only"] += 1
        else:
            counts["untrimmed"] += 1
        counts["canonical_orientation"] += canonical
        counts["reverse_orientation"] += reverse
        counts["confidence_high"] += high
        counts["confidence_medium"] += medium
        counts["confidence_low"] += low
        counts["dropped_empty"] += dropped
        if trimmed_seq is not None:
            trimmed_records.append((header, trimmed_seq))
    before = len(records)
    summary = {
        "round": round_idx,
        "records": trimmed_records,
        "before": before,
        "after": len(trimmed_records),
        "removed": before - len(trimmed_records),
        **counts,
        "high_conf_rate": counts["confidence_high"] / before if before else 0.0,
        "round_max_mismatch": max_mismatch,
        "round_min_overlap_ratio": overlap_ratio,
        "round_min_overlap_bp": overlap_bp or 0,
    }
    return _PrimerRoundResult(trimmed_records, rows, summary)


def _run_primer_trim_rounds(
    ctx: _PrimerTrimContext, records: List[Tuple[str, str]]
) -> Tuple[List[_PrimerRoundResult], List[Dict[str, Any]]]:
    round_limit = max(1, ctx.iter_max_rounds if ctx.iter_enable else 1)
    results: List[_PrimerRoundResult] = []
    all_rows: List[Dict[str, Any]] = []
    previous_rate: Optional[float] = None
    for round_idx in range(1, round_limit + 1):
        result = _run_primer_trim_round(ctx, records, round_idx)
        results.append(result)
        all_rows.extend(result.rows)
        high_rate = result.summary["high_conf_rate"]
        if not ctx.iter_enable:
            break
        if high_rate >= ctx.iter_target_conf:
            break
        if previous_rate is not None and high_rate - previous_rate < ctx.iter_stop_delta:
            break
        previous_rate = high_rate
    return results, all_rows


def _apply_primer_recheck(
    ctx: _PrimerTrimContext,
    best_summary: Dict[str, Any],
    sidecar_rows: List[Dict[str, Any]],
    records: List[Tuple[str, str]],
) -> Tuple[int, int, Optional[str]]:
    if ctx.recheck_tool != "vsearch":
        return 0, 0, f"unsupported_tool:{ctx.recheck_tool}" if ctx.recheck_tool != "off" else None
    seq_by_header = {header: seq for header, seq in records}
    attempted, rescued, error = run_vsearch_endpoint_recheck(
        sidecar_rows, seq_by_header, ctx.forward_primers, ctx.reverse_primers,
        ctx.forward_rc, ctx.reverse_rc, min_identity=ctx.recheck_min_identity,
        min_query_cov=ctx.recheck_min_query_cov,
    )
    rebuilt_records, rebuilt_summary = summarize_rows_to_records(
        sidecar_rows, seq_by_header, ctx.trim_mode
    )
    best_summary.update(rebuilt_summary)
    best_summary["records"] = rebuilt_records
    return attempted, rescued, error


def _build_primer_trim_result(
    ctx: _PrimerTrimContext,
    best_summary: Dict[str, Any],
    round_results: List[_PrimerRoundResult],
    sidecar_path: Optional[Path],
    recheck_attempted: int,
    recheck_rescued: int,
    recheck_error: Optional[str],
) -> Dict[str, Any]:
    return {
        "before": int(best_summary["before"]), "after": int(best_summary["after"]),
        "removed": int(best_summary["removed"]), "trimmed_both": int(best_summary["trimmed_both"]),
        "trimmed_left_only": int(best_summary["trimmed_left_only"]),
        "trimmed_right_only": int(best_summary["trimmed_right_only"]),
        "untrimmed": int(best_summary["untrimmed"]), "dropped_empty": int(best_summary["dropped_empty"]),
        "canonical_orientation": int(best_summary["canonical_orientation"]),
        "reverse_orientation": int(best_summary["reverse_orientation"]),
        "confidence_high": int(best_summary["confidence_high"]),
        "confidence_medium": int(best_summary["confidence_medium"]),
        "confidence_low": int(best_summary["confidence_low"]),
        "high_conf_rate": float(best_summary["high_conf_rate"]),
        "rounds_run": len(round_results), "best_round": int(best_summary["round"]),
        "sidecar_path": str(sidecar_path) if sidecar_path else None,
        "retained_path": str(ctx.retained_path) if ctx.keep_retained_fasta else None,
        "recheck_tool": ctx.recheck_tool, "recheck_attempted": recheck_attempted,
        "recheck_rescued": recheck_rescued, "recheck_error": recheck_error,
        "phylo_target_confidence": ctx.phylo_target_confidence,
    }


def apply_post_prep_primer_trim(
    fasta_path: Path,
    forward_primers: List[str],
    reverse_primers: List[str],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ctx = _build_primer_trim_context(fasta_path, forward_primers, reverse_primers, options)
    records = _prepare_primer_trim(ctx)
    if not records:
        return _empty_primer_trim_result(ctx)

    round_results, all_round_rows = _run_primer_trim_rounds(ctx, records)
    best_summary = max(
        (result.summary for result in round_results),
        key=lambda row: (row["high_conf_rate"], row["after"], -row["dropped_empty"], row["round"]),
    )
    best_round = int(best_summary["round"])
    sidecar_rows = [dict(row) for row in all_round_rows if int(row["round"]) == best_round]
    recheck_attempted, recheck_rescued, recheck_error = _apply_primer_recheck(
        ctx, best_summary, sidecar_rows, records
    )
    sidecar_path = _write_primer_outputs(
        fasta_path, best_summary["records"], sidecar_rows, ctx.sidecar_format
    )
    return _build_primer_trim_result(
        ctx, best_summary, round_results, sidecar_path,
        recheck_attempted, recheck_rescued, recheck_error,
    )

"""Configuration loading and primer support-file helpers."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from .models import (
    BuildSource,
    IUPAC_DNA_VALUES,
    PRIMER_TRIM_MODE_ONE_OR_BOTH,
    PRIMER_TRIM_MODES,
)

def load_config(path: Path, source: BuildSource = BuildSource.NCBI) -> Dict:
    if not path.exists():
        raise typer.BadParameter(f"Config file not found: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)

    if "markers_file" in data:
        raise typer.BadParameter("markers_file must be defined under [markers].file (top-level is not supported).")

    markers_file = None
    markers_section = data.get("markers")
    if isinstance(markers_section, dict):
        if "markers_file" in markers_section:
            raise typer.BadParameter("Use [markers].file instead of [markers].markers_file.")
        if "file" in markers_section:
            markers_file = markers_section.get("file")
            if not isinstance(markers_file, str):
                raise typer.BadParameter("markers.file must be a string path.")

    inline_markers: Dict[str, Dict] = {}
    if markers_section is None:
        markers_section = {}
    if not isinstance(markers_section, dict):
        raise typer.BadParameter("[markers] must be a table (dict).")
    for key, value in markers_section.items():
        if key in ("file", "markers_file"):
            continue
        if not isinstance(value, dict):
            raise typer.BadParameter(f"markers.{key} must be a table (dict).")
        inline_markers[key] = value

    markers_from_file: Dict[str, Dict] = {}
    if markers_file:
        if not isinstance(markers_file, str):
            raise typer.BadParameter("markers.file must be a string path.")
        markers_path = Path(os.path.expandvars(os.path.expanduser(markers_file)))
        candidates: List[Path] = []
        if markers_path.is_absolute():
            candidates.append(markers_path)
        else:
            candidates.append(path.parent / markers_path)
            candidates.append(Path.cwd() / markers_path)
            candidates.append(Path(__file__).resolve().parent.parent / markers_path)

        markers_path = next((p for p in candidates if p.exists()), None)
        if not markers_path:
            tried = ", ".join(str(p) for p in candidates)
            raise typer.BadParameter(f"Markers file not found. Tried: {tried}")
        with markers_path.open("rb") as f:
            markers_data = tomllib.load(f)
        markers_from_file = markers_data.get("markers")
        if not isinstance(markers_from_file, dict) or not markers_from_file:
            raise typer.BadParameter("Markers file must define a non-empty [markers] section.")

    merged = {}
    merged.update(markers_from_file)
    merged.update(inline_markers)
    if merged:
        data["markers"] = merged

    requires_ncbi = source in {BuildSource.NCBI, BuildSource.BOTH}
    if requires_ncbi and "ncbi" not in data:
        raise typer.BadParameter("Missing [ncbi] section in config.")
    if "markers" not in data or not data["markers"]:
        raise typer.BadParameter("Missing [markers] section in config.")
    bold_cfg = data.get("bold")
    if bold_cfg is not None and not isinstance(bold_cfg, dict):
        raise typer.BadParameter("[bold] must be a table (dict).")
    if source == BuildSource.BOTH and bold_cfg is None:
        data["bold"] = {}

    post_prep = data.get("post_prep")
    if post_prep is not None:
        if not isinstance(post_prep, dict):
            raise typer.BadParameter("[post_prep] must be a table (dict).")

        def parse_int_option(name: str, raw: Any, min_value: Optional[int] = None) -> int:
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise typer.BadParameter(f"{name} must be an integer.") from exc
            if min_value is not None and value < min_value:
                raise typer.BadParameter(f"{name} must be >= {min_value}.")
            return value

        def parse_float_option(
            name: str,
            raw: Any,
            min_value: Optional[float] = None,
            max_value: Optional[float] = None,
        ) -> float:
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise typer.BadParameter(f"{name} must be a number.") from exc
            if min_value is not None and value < min_value:
                raise typer.BadParameter(f"{name} must be >= {min_value}.")
            if max_value is not None and value > max_value:
                raise typer.BadParameter(f"{name} must be <= {max_value}.")
            return value

        def parse_bool_option(name: str, raw: Any) -> bool:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                value = raw.strip().lower()
                if value in {"1", "true", "yes", "on"}:
                    return True
                if value in {"0", "false", "no", "off"}:
                    return False
            raise typer.BadParameter(f"{name} must be a boolean.")

        min_len = post_prep.get("sequence_length_min")
        max_len = post_prep.get("sequence_length_max")
        if min_len is not None:
            min_len = parse_int_option("post_prep.sequence_length_min", min_len)
            post_prep["sequence_length_min"] = min_len
        if max_len is not None:
            max_len = parse_int_option("post_prep.sequence_length_max", max_len)
            post_prep["sequence_length_max"] = max_len
        if min_len is not None and max_len is not None and min_len > max_len:
            raise typer.BadParameter("post_prep.sequence_length_min must be <= post_prep.sequence_length_max.")

        primer_file = post_prep.get("primer_file")
        primer_set_raw = post_prep.get("primer_set")
        primer_set_list: Optional[List[str]] = None
        if primer_file is not None:
            if not isinstance(primer_file, str) or not primer_file.strip():
                raise typer.BadParameter("post_prep.primer_file must be a non-empty string path.")
            primer_file = primer_file.strip()
            post_prep["primer_file"] = primer_file
        if primer_set_raw is not None:
            if isinstance(primer_set_raw, str):
                primer_set_list = [primer_set_raw]
            elif isinstance(primer_set_raw, list) and all(isinstance(v, str) for v in primer_set_raw):
                primer_set_list = list(primer_set_raw)
            else:
                raise typer.BadParameter("post_prep.primer_set must be a string or list of strings.")

            normalized_sets: List[str] = []
            for value in primer_set_list:
                name = value.strip()
                if not name:
                    raise typer.BadParameter("post_prep.primer_set cannot contain empty values.")
                if name not in normalized_sets:
                    normalized_sets.append(name)
            primer_set_list = normalized_sets
            post_prep["primer_set"] = primer_set_list

        if primer_set_list is not None and primer_file is None:
            raise typer.BadParameter("post_prep.primer_set requires post_prep.primer_file.")

        primer_max_mismatch_raw = post_prep.get("primer_max_mismatch", 0)
        primer_max_error_rate_raw = post_prep.get("primer_max_error_rate", 0.0)
        primer_min_overlap_bp_raw = post_prep.get("primer_min_overlap_bp")
        primer_min_overlap_ratio_raw = post_prep.get("primer_min_overlap_ratio", 1.0)
        primer_end_max_offset_raw = post_prep.get("primer_end_max_offset", 0)
        primer_trim_mode_raw = post_prep.get("primer_trim_mode", PRIMER_TRIM_MODE_ONE_OR_BOTH)
        primer_keep_retained_raw = post_prep.get("primer_keep_retained_fasta", True)
        primer_iter_enable_raw = post_prep.get("primer_iter_enable", False)
        primer_iter_max_rounds_raw = post_prep.get("primer_iter_max_rounds", 3)
        primer_iter_stop_delta_raw = post_prep.get("primer_iter_stop_delta", 0.002)
        primer_iter_target_conf_raw = post_prep.get("primer_iter_target_conf", 0.98)
        primer_recheck_tool_raw = post_prep.get("primer_recheck_tool", "off")
        primer_recheck_min_identity_raw = post_prep.get("primer_recheck_min_identity", 0.85)
        primer_recheck_min_query_cov_raw = post_prep.get("primer_recheck_min_query_cov", 0.7)
        primer_phylo_check_raw = post_prep.get("primer_phylo_check", "off")
        primer_phylo_target_raw = post_prep.get("primer_phylo_target_confidence", "medium")
        primer_sidecar_format_raw = post_prep.get("primer_sidecar_format", "tsv")

        primer_max_mismatch = parse_int_option("post_prep.primer_max_mismatch", primer_max_mismatch_raw, 0)
        primer_max_error_rate = parse_float_option(
            "post_prep.primer_max_error_rate", primer_max_error_rate_raw, 0.0, 1.0
        )
        primer_min_overlap_bp = None
        if primer_min_overlap_bp_raw is not None:
            primer_min_overlap_bp = parse_int_option("post_prep.primer_min_overlap_bp", primer_min_overlap_bp_raw, 1)
        primer_min_overlap_ratio = parse_float_option(
            "post_prep.primer_min_overlap_ratio", primer_min_overlap_ratio_raw, 0.0, 1.0
        )
        primer_end_max_offset = parse_int_option("post_prep.primer_end_max_offset", primer_end_max_offset_raw, 0)

        if not isinstance(primer_trim_mode_raw, str):
            raise typer.BadParameter("post_prep.primer_trim_mode must be a string.")
        primer_trim_mode = primer_trim_mode_raw.strip().lower()
        if primer_trim_mode not in PRIMER_TRIM_MODES:
            modes = ", ".join(sorted(PRIMER_TRIM_MODES))
            raise typer.BadParameter(f"post_prep.primer_trim_mode must be one of: {modes}")

        primer_keep_retained_fasta = parse_bool_option("post_prep.primer_keep_retained_fasta", primer_keep_retained_raw)
        primer_iter_enable = parse_bool_option("post_prep.primer_iter_enable", primer_iter_enable_raw)
        primer_iter_max_rounds = parse_int_option("post_prep.primer_iter_max_rounds", primer_iter_max_rounds_raw, 1)
        primer_iter_stop_delta = parse_float_option("post_prep.primer_iter_stop_delta", primer_iter_stop_delta_raw, 0.0)
        primer_iter_target_conf = parse_float_option(
            "post_prep.primer_iter_target_conf", primer_iter_target_conf_raw, 0.0, 1.0
        )

        if not isinstance(primer_recheck_tool_raw, str):
            raise typer.BadParameter("post_prep.primer_recheck_tool must be a string.")
        primer_recheck_tool = primer_recheck_tool_raw.strip().lower()
        if primer_recheck_tool not in {"off", "vsearch", "blast"}:
            raise typer.BadParameter("post_prep.primer_recheck_tool must be one of: off, vsearch, blast")
        primer_recheck_min_identity = parse_float_option(
            "post_prep.primer_recheck_min_identity", primer_recheck_min_identity_raw, 0.0, 1.0
        )
        primer_recheck_min_query_cov = parse_float_option(
            "post_prep.primer_recheck_min_query_cov", primer_recheck_min_query_cov_raw, 0.0, 1.0
        )

        if not isinstance(primer_phylo_check_raw, str):
            raise typer.BadParameter("post_prep.primer_phylo_check must be a string.")
        primer_phylo_check = primer_phylo_check_raw.strip().lower()
        if primer_phylo_check not in {"off", "flag_only"}:
            raise typer.BadParameter("post_prep.primer_phylo_check must be one of: off, flag_only")

        if not isinstance(primer_phylo_target_raw, str):
            raise typer.BadParameter("post_prep.primer_phylo_target_confidence must be a string.")
        primer_phylo_target = primer_phylo_target_raw.strip().lower()
        if primer_phylo_target not in {"low", "medium"}:
            raise typer.BadParameter("post_prep.primer_phylo_target_confidence must be one of: low, medium")

        if not isinstance(primer_sidecar_format_raw, str):
            raise typer.BadParameter("post_prep.primer_sidecar_format must be a string.")
        primer_sidecar_format = primer_sidecar_format_raw.strip().lower()
        if primer_sidecar_format not in {"tsv", "jsonl"}:
            raise typer.BadParameter("post_prep.primer_sidecar_format must be one of: tsv, jsonl")

        post_prep["primer_max_mismatch"] = primer_max_mismatch
        post_prep["primer_max_error_rate"] = primer_max_error_rate
        post_prep["primer_min_overlap_bp"] = primer_min_overlap_bp
        post_prep["primer_min_overlap_ratio"] = primer_min_overlap_ratio
        post_prep["primer_end_max_offset"] = primer_end_max_offset
        post_prep["primer_trim_mode"] = primer_trim_mode
        post_prep["primer_keep_retained_fasta"] = primer_keep_retained_fasta
        post_prep["primer_iter_enable"] = primer_iter_enable
        post_prep["primer_iter_max_rounds"] = primer_iter_max_rounds
        post_prep["primer_iter_stop_delta"] = primer_iter_stop_delta
        post_prep["primer_iter_target_conf"] = primer_iter_target_conf
        post_prep["primer_recheck_tool"] = primer_recheck_tool
        post_prep["primer_recheck_min_identity"] = primer_recheck_min_identity
        post_prep["primer_recheck_min_query_cov"] = primer_recheck_min_query_cov
        post_prep["primer_phylo_check"] = primer_phylo_check
        post_prep["primer_phylo_target_confidence"] = primer_phylo_target
        post_prep["primer_sidecar_format"] = primer_sidecar_format

        if primer_file and primer_set_list:
            primer_path = resolve_support_file_path(primer_file, path, "Primer file")
            primer_sets_data = load_primer_sets_from_file(primer_path)
            forward, reverse = combine_primer_set_sequences(primer_sets_data, primer_set_list)

            post_prep["_primer_forward"] = forward
            post_prep["_primer_reverse"] = reverse
            post_prep["_primer_file_resolved"] = str(primer_path)
            post_prep["_primer_set_names"] = primer_set_list
            post_prep["_primer_set_candidates"] = sorted(primer_sets_data.keys())
        elif primer_file:
            primer_path = resolve_support_file_path(primer_file, path, "Primer file")
            primer_sets_data = load_primer_sets_from_file(primer_path)
            post_prep["_primer_file_resolved"] = str(primer_path)
            post_prep["_primer_set_candidates"] = sorted(primer_sets_data.keys())

    return data




def resolve_support_file_path(raw_path: str, config_path: Path, label: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    candidates: List[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(config_path.parent / path)
        candidates.append(Path.cwd() / path)
        candidates.append(Path(__file__).resolve().parent.parent / path)

    resolved = next((p for p in candidates if p.exists()), None)
    if not resolved:
        tried = ", ".join(str(p) for p in candidates)
        raise typer.BadParameter(f"{label} not found. Tried: {tried}")
    return resolved


def normalize_primer_values(values: List[str], primer_set_name: str, field_name: str) -> List[str]:
    normalized: List[str] = []
    for value in values:
        primer = value.strip().upper().replace("U", "T")
        if not primer:
            raise typer.BadParameter(f"primer_sets.{primer_set_name}.{field_name} cannot contain empty primers.")
        invalid = sorted({ch for ch in primer if ch not in IUPAC_DNA_VALUES})
        if invalid:
            chars = "".join(invalid)
            raise typer.BadParameter(
                f"primer_sets.{primer_set_name}.{field_name} contains unsupported IUPAC chars: {chars}"
            )
        normalized.append(primer)
    return normalized


def load_primer_sets_from_file(primer_path: Path) -> Dict[str, Dict[str, List[str]]]:
    with primer_path.open("rb") as f:
        primer_data = tomllib.load(f)
    primer_sets = primer_data.get("primer_sets")
    if not isinstance(primer_sets, dict) or not primer_sets:
        raise typer.BadParameter("Primer file must define a non-empty [primer_sets] section.")

    normalized_sets: Dict[str, Dict[str, List[str]]] = {}
    for set_name, set_cfg in primer_sets.items():
        if not isinstance(set_cfg, dict):
            raise typer.BadParameter(f"primer_sets.{set_name} must be a table (dict).")
        forward = set_cfg.get("forward")
        reverse = set_cfg.get("reverse")
        if not isinstance(forward, list) or not forward or not all(isinstance(v, str) for v in forward):
            raise typer.BadParameter(f"primer_sets.{set_name}.forward must be a non-empty list of strings.")
        if not isinstance(reverse, list) or not reverse or not all(isinstance(v, str) for v in reverse):
            raise typer.BadParameter(f"primer_sets.{set_name}.reverse must be a non-empty list of strings.")
        normalized_sets[set_name] = {
            "forward": normalize_primer_values(forward, set_name, "forward"),
            "reverse": normalize_primer_values(reverse, set_name, "reverse"),
        }
    return normalized_sets


def combine_primer_set_sequences(
    primer_sets_data: Dict[str, Dict[str, List[str]]],
    selected_sets: List[str],
) -> Tuple[List[str], List[str]]:
    forward: List[str] = []
    reverse: List[str] = []
    for set_name in selected_sets:
        set_data = primer_sets_data.get(set_name)
        if not set_data:
            available = ", ".join(sorted(primer_sets_data.keys()))
            raise typer.BadParameter(f"Primer set '{set_name}' was not found. Available: {available}")
        for p in set_data["forward"]:
            if p not in forward:
                forward.append(p)
        for p in set_data["reverse"]:
            if p not in reverse:
                reverse.append(p)
    return forward, reverse




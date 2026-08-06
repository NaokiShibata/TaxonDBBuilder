"""Configuration loading and primer support-file helpers."""

import os
from pathlib import Path
from typing import Any

import typer

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from .models import (
    IUPAC_DNA_VALUES,
    PRIMER_TRIM_MODE_ONE_OR_BOTH,
    PRIMER_TRIM_MODES,
    BuildSource,
)


BUILTIN_PRIMER_SETS: dict[str, dict[str, list[str]]] = {
    "mifish_u": {
        "forward": ["GTCGGTAAAACTCGTGCCAGC"],
        "reverse": ["CATAGTGGGGTATCTAATCCCAGTTTG"],
    },
    "mideca": {
        "forward": ["GGACGATAAGACCCTATAAA"],
        "reverse": ["ACGCTGTTATCCCTAAAGT"],
    },
    "mtinsects_16s": {
        "forward": ["GGACGAGAAGACCCTWTAGA"],
        "reverse": ["ATCCAACATCGAGGTCGCAA"],
    },
    "mimammal_u": {
        "forward": ["GGGTTGGTAAATTTCGTGCCAGC"],
        "reverse": ["CATAGTGGGGTATCTAATCCCAGTTTG"],
    },
    "mibird_u": {
        "forward": ["GGGTTGGTAAATCTTGTGCCAGC"],
        "reverse": ["CATAGTGGGGTATCTAATCCCAGTTTG"],
    },
    "amph_16s": {
        "forward": ["ACGAGAAGACCCYRTGGARCTT"],
        "reverse": ["ATCCAACATCGAGGTCGTAA"],
    },
    "uceta": {
        "forward": ["GACACTGAAAATGTCTAGATGG"],
        "reverse": ["TYAATCGTATGACCGCGGTG"],
    },
    "mifish_unity": {
        "forward": ["GYYGGTAAAWCTCGTGCCAGC"],
        "reverse": ["CATAGKRGGGTRTCTAATCCYMGTTTG"],
    },
}


def _parse_int_option(name: str, raw: Any, min_value: int | None = None) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(f"{name} must be an integer.") from exc
    if min_value is not None and value < min_value:
        raise typer.BadParameter(f"{name} must be >= {min_value}.")
    return value


def _parse_float_option(
    name: str,
    raw: Any,
    min_value: float | None = None,
    max_value: float | None = None,
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


def _parse_bool_option(name: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    raise typer.BadParameter(f"{name} must be a boolean.")


def _normalize_length_filter_config(post_prep: dict[str, Any]) -> None:
    min_len = post_prep.get("sequence_length_min")
    max_len = post_prep.get("sequence_length_max")
    if min_len is not None:
        min_len = _parse_int_option("post_prep.sequence_length_min", min_len)
        post_prep["sequence_length_min"] = min_len
    if max_len is not None:
        max_len = _parse_int_option("post_prep.sequence_length_max", max_len)
        post_prep["sequence_length_max"] = max_len
    if min_len is not None and max_len is not None and min_len > max_len:
        raise typer.BadParameter(
            "post_prep.sequence_length_min must be <= post_prep.sequence_length_max."
        )


def _normalize_primer_selection(
    post_prep: dict[str, Any],
) -> tuple[str | None, list[str] | None]:
    primer_file = post_prep.get("primer_file")
    primer_set_raw = post_prep.get("primer_set")
    primer_set_list: list[str] | None = None
    if primer_file is not None:
        if not isinstance(primer_file, str) or not primer_file.strip():
            raise typer.BadParameter(
                "post_prep.primer_file must be a non-empty string path."
            )
        primer_file = primer_file.strip()
        post_prep["primer_file"] = primer_file
    if primer_set_raw is not None:
        if isinstance(primer_set_raw, str):
            primer_set_list = [primer_set_raw]
        elif isinstance(primer_set_raw, list) and all(
            isinstance(v, str) for v in primer_set_raw
        ):
            primer_set_list = list(primer_set_raw)
        else:
            raise typer.BadParameter(
                "post_prep.primer_set must be a string or list of strings."
            )
        normalized_sets: list[str] = []
        for value in primer_set_list:
            name = value.strip()
            if not name:
                raise typer.BadParameter(
                    "post_prep.primer_set cannot contain empty values."
                )
            if name not in normalized_sets:
                normalized_sets.append(name)
        primer_set_list = normalized_sets
        post_prep["primer_set"] = primer_set_list
    return primer_file, primer_set_list


def _parse_primer_numeric_options(post_prep: dict[str, Any]) -> dict[str, Any]:
    options = {
        "max_mismatch": _parse_int_option(
            "post_prep.primer_max_mismatch", post_prep.get("primer_max_mismatch", 0), 0
        ),
        "max_error_rate": _parse_float_option(
            "post_prep.primer_max_error_rate",
            post_prep.get("primer_max_error_rate", 0.0),
            0.0,
            1.0,
        ),
    }
    min_overlap_raw = post_prep.get("primer_min_overlap_bp")
    options["min_overlap_bp"] = (
        _parse_int_option("post_prep.primer_min_overlap_bp", min_overlap_raw, 1)
        if min_overlap_raw is not None
        else None
    )
    options["min_overlap_ratio"] = _parse_float_option(
        "post_prep.primer_min_overlap_ratio",
        post_prep.get("primer_min_overlap_ratio", 1.0),
        0.0,
        1.0,
    )
    options["end_max_offset"] = _parse_int_option(
        "post_prep.primer_end_max_offset", post_prep.get("primer_end_max_offset", 0), 0
    )
    return options


def _parse_primer_mode_options(post_prep: dict[str, Any]) -> dict[str, Any]:
    mode_raw = post_prep.get("primer_trim_mode", PRIMER_TRIM_MODE_ONE_OR_BOTH)
    if not isinstance(mode_raw, str):
        raise typer.BadParameter("post_prep.primer_trim_mode must be a string.")
    mode = mode_raw.strip().lower()
    if mode not in PRIMER_TRIM_MODES:
        modes = ", ".join(sorted(PRIMER_TRIM_MODES))
        raise typer.BadParameter(f"post_prep.primer_trim_mode must be one of: {modes}")
    return {
        "trim_mode": mode,
        "keep_retained_fasta": _parse_bool_option(
            "post_prep.primer_keep_retained_fasta",
            post_prep.get("primer_keep_retained_fasta", True),
        ),
        "iter_enable": _parse_bool_option(
            "post_prep.primer_iter_enable", post_prep.get("primer_iter_enable", False)
        ),
        "iter_max_rounds": _parse_int_option(
            "post_prep.primer_iter_max_rounds",
            post_prep.get("primer_iter_max_rounds", 3),
            1,
        ),
        "iter_stop_delta": _parse_float_option(
            "post_prep.primer_iter_stop_delta",
            post_prep.get("primer_iter_stop_delta", 0.002),
            0.0,
        ),
        "iter_target_conf": _parse_float_option(
            "post_prep.primer_iter_target_conf",
            post_prep.get("primer_iter_target_conf", 0.98),
            0.0,
            1.0,
        ),
    }


def _parse_primer_recheck_options(post_prep: dict[str, Any]) -> dict[str, Any]:
    tool_raw = post_prep.get("primer_recheck_tool", "off")
    if not isinstance(tool_raw, str):
        raise typer.BadParameter("post_prep.primer_recheck_tool must be a string.")
    tool = tool_raw.strip().lower()
    if tool not in {"off", "vsearch", "blast"}:
        raise typer.BadParameter(
            "post_prep.primer_recheck_tool must be one of: off, vsearch, blast"
        )
    return {
        "recheck_tool": tool,
        "recheck_min_identity": _parse_float_option(
            "post_prep.primer_recheck_min_identity",
            post_prep.get("primer_recheck_min_identity", 0.85),
            0.0,
            1.0,
        ),
        "recheck_min_query_cov": _parse_float_option(
            "post_prep.primer_recheck_min_query_cov",
            post_prep.get("primer_recheck_min_query_cov", 0.7),
            0.0,
            1.0,
        ),
    }


def _parse_primer_reporting_options(post_prep: dict[str, Any]) -> dict[str, str]:
    phylo_raw = post_prep.get("primer_phylo_check", "off")
    if not isinstance(phylo_raw, str):
        raise typer.BadParameter("post_prep.primer_phylo_check must be a string.")
    phylo = phylo_raw.strip().lower()
    if phylo not in {"off", "flag_only"}:
        raise typer.BadParameter(
            "post_prep.primer_phylo_check must be one of: off, flag_only"
        )
    target_raw = post_prep.get("primer_phylo_target_confidence", "medium")
    if not isinstance(target_raw, str):
        raise typer.BadParameter(
            "post_prep.primer_phylo_target_confidence must be a string."
        )
    target = target_raw.strip().lower()
    if target not in {"low", "medium"}:
        raise typer.BadParameter(
            "post_prep.primer_phylo_target_confidence must be one of: low, medium"
        )
    sidecar_raw = post_prep.get("primer_sidecar_format", "tsv")
    if not isinstance(sidecar_raw, str):
        raise typer.BadParameter("post_prep.primer_sidecar_format must be a string.")
    sidecar = sidecar_raw.strip().lower()
    if sidecar not in {"tsv", "jsonl"}:
        raise typer.BadParameter(
            "post_prep.primer_sidecar_format must be one of: tsv, jsonl"
        )
    return {
        "phylo_check": phylo,
        "phylo_target_confidence": target,
        "sidecar_format": sidecar,
    }


def _parse_msa_tree_options(post_prep: dict[str, Any]) -> dict[str, Any]:
    model_raw = post_prep.get("msa_tree_model", "GTR+G")
    if not isinstance(model_raw, str) or not model_raw.strip():
        raise typer.BadParameter("post_prep.msa_tree_model must be a non-empty string.")
    min_taxa = _parse_int_option(
        "post_prep.msa_tree_min_taxa", post_prep.get("msa_tree_min_taxa", 3), 0
    )
    max_samples = _parse_int_option(
        "post_prep.msa_tree_max_samples",
        post_prep.get("msa_tree_max_samples", 500),
        0,
    )
    if min_taxa > max_samples:
        raise typer.BadParameter(
            "post_prep.msa_tree_min_taxa must be <= post_prep.msa_tree_max_samples."
        )
    return {
        "enable": _parse_bool_option(
            "post_prep.msa_tree_enable",
            post_prep.get("msa_tree_enable", False),
        ),
        "min_taxa": min_taxa,
        "max_samples": max_samples,
        "model": model_raw.strip(),
    }


def _normalize_primer_trim_config(post_prep: dict[str, Any], path: Path) -> None:
    primer_file, primer_sets = _normalize_primer_selection(post_prep)
    options = _parse_primer_numeric_options(post_prep)
    options.update(_parse_primer_mode_options(post_prep))
    options.update(_parse_primer_recheck_options(post_prep))
    options.update(_parse_primer_reporting_options(post_prep))
    post_prep.update({f"primer_{key}": value for key, value in options.items()})
    if primer_file is None and primer_sets is None:
        return
    primer_sets_data = dict(BUILTIN_PRIMER_SETS)
    if primer_file is not None:
        primer_path = resolve_support_file_path(primer_file, path, "Primer file")
        primer_sets_data.update(load_primer_sets_from_file(primer_path))
        post_prep["_primer_file_resolved"] = str(primer_path)
    post_prep["_primer_set_candidates"] = sorted(primer_sets_data.keys())
    if primer_sets:
        forward, reverse = combine_primer_set_sequences(primer_sets_data, primer_sets)
        post_prep["_primer_forward"] = forward
        post_prep["_primer_reverse"] = reverse
        post_prep["_primer_set_names"] = primer_sets


def _normalize_post_prep_config(post_prep: Any, path: Path) -> None:
    if post_prep is None:
        return
    if not isinstance(post_prep, dict):
        raise typer.BadParameter("[post_prep] must be a table (dict).")
    _normalize_length_filter_config(post_prep)
    _normalize_primer_trim_config(post_prep, path)
    msa_tree_options = _parse_msa_tree_options(post_prep)
    post_prep.update(
        {f"msa_tree_{key}": value for key, value in msa_tree_options.items()}
    )


def load_config(path: Path, source: BuildSource = BuildSource.NCBI) -> dict:
    if not path.exists():
        raise typer.BadParameter(f"Config file not found: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)

    if "markers_file" in data:
        raise typer.BadParameter(
            "markers_file must be defined under [markers].file (top-level is not supported)."
        )

    markers_file = None
    markers_section = data.get("markers")
    if isinstance(markers_section, dict):
        if "markers_file" in markers_section:
            raise typer.BadParameter(
                "Use [markers].file instead of [markers].markers_file."
            )
        if "file" in markers_section:
            markers_file = markers_section.get("file")
            if not isinstance(markers_file, str):
                raise typer.BadParameter("markers.file must be a string path.")

    inline_markers: dict[str, dict] = {}
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

    markers_from_file: dict[str, dict] = {}
    if markers_file:
        if not isinstance(markers_file, str):
            raise typer.BadParameter("markers.file must be a string path.")
        markers_path = Path(os.path.expandvars(os.path.expanduser(markers_file)))
        candidates: list[Path] = []
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
            raise typer.BadParameter(
                "Markers file must define a non-empty [markers] section."
            )

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
    _normalize_post_prep_config(post_prep, path)

    return data


def resolve_support_file_path(raw_path: str, config_path: Path, label: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    candidates: list[Path] = []
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


def normalize_primer_values(
    values: list[str], primer_set_name: str, field_name: str
) -> list[str]:
    normalized: list[str] = []
    for value in values:
        primer = value.strip().upper().replace("U", "T")
        if not primer:
            raise typer.BadParameter(
                f"primer_sets.{primer_set_name}.{field_name} cannot contain empty primers."
            )
        invalid = sorted({ch for ch in primer if ch not in IUPAC_DNA_VALUES})
        if invalid:
            chars = "".join(invalid)
            raise typer.BadParameter(
                f"primer_sets.{primer_set_name}.{field_name} contains unsupported IUPAC chars: {chars}"
            )
        normalized.append(primer)
    return normalized


def load_primer_sets_from_file(primer_path: Path) -> dict[str, dict[str, list[str]]]:
    with primer_path.open("rb") as f:
        primer_data = tomllib.load(f)
    primer_sets = primer_data.get("primer_sets")
    if not isinstance(primer_sets, dict) or not primer_sets:
        raise typer.BadParameter(
            "Primer file must define a non-empty [primer_sets] section."
        )

    normalized_sets: dict[str, dict[str, list[str]]] = {}
    for set_name, set_cfg in primer_sets.items():
        if not isinstance(set_cfg, dict):
            raise typer.BadParameter(f"primer_sets.{set_name} must be a table (dict).")
        forward = set_cfg.get("forward")
        reverse = set_cfg.get("reverse")
        if (
            not isinstance(forward, list)
            or not forward
            or not all(isinstance(v, str) for v in forward)
        ):
            raise typer.BadParameter(
                f"primer_sets.{set_name}.forward must be a non-empty list of strings."
            )
        if (
            not isinstance(reverse, list)
            or not reverse
            or not all(isinstance(v, str) for v in reverse)
        ):
            raise typer.BadParameter(
                f"primer_sets.{set_name}.reverse must be a non-empty list of strings."
            )
        normalized_sets[set_name] = {
            "forward": normalize_primer_values(forward, set_name, "forward"),
            "reverse": normalize_primer_values(reverse, set_name, "reverse"),
        }
    return normalized_sets


def combine_primer_set_sequences(
    primer_sets_data: dict[str, dict[str, list[str]]],
    selected_sets: list[str],
) -> tuple[list[str], list[str]]:
    forward: list[str] = []
    reverse: list[str] = []
    for set_name in selected_sets:
        set_data = primer_sets_data.get(set_name)
        if not set_data:
            available = ", ".join(sorted(primer_sets_data.keys()))
            raise typer.BadParameter(
                f"Primer set '{set_name}' was not found. Available: {available}"
            )
        for p in set_data["forward"]:
            if p not in forward:
                forward.append(p)
        for p in set_data["reverse"]:
            if p not in reverse:
                reverse.append(p)
    return forward, reverse

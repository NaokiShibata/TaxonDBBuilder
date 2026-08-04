"""Marker map normalization and query helpers."""

import re
from typing import Dict, List

import typer

from .models import BuildSource

def normalize_marker_map(markers_cfg: Dict, source: BuildSource = BuildSource.NCBI) -> Dict[str, Dict]:
    marker_map: Dict[str, Dict] = {}
    uses_ncbi = source in {BuildSource.NCBI, BuildSource.BOTH}
    uses_bold = source in {BuildSource.BOLD, BuildSource.BOTH}
    for key, cfg in markers_cfg.items():
        phrases = cfg.get("phrases") or []
        terms = cfg.get("terms") or []
        region_patterns = cfg.get("region_patterns") or []
        header_format = cfg.get("header_format")
        feature_types = cfg.get("feature_types")
        feature_fields = cfg.get("feature_fields")
        aliases = cfg.get("aliases") or []
        bold_cfg = cfg.get("bold") or {}
        if not isinstance(phrases, list):
            raise typer.BadParameter(f"markers.{key}.phrases must be a list of strings.")
        if any(not isinstance(item, str) for item in phrases):
            raise typer.BadParameter(f"markers.{key}.phrases must contain only strings.")
        if not isinstance(terms, list):
            raise typer.BadParameter(f"markers.{key}.terms must be a list of strings.")
        if any(not isinstance(item, str) for item in terms):
            raise typer.BadParameter(f"markers.{key}.terms must contain only strings.")
        if not isinstance(region_patterns, list):
            raise typer.BadParameter(f"markers.{key}.region_patterns must be a list of strings.")
        if any(not isinstance(item, str) for item in region_patterns):
            raise typer.BadParameter(f"markers.{key}.region_patterns must contain only strings.")
        if not isinstance(aliases, list):
            raise typer.BadParameter(f"markers.{key}.aliases must be a list of strings.")
        if any(not isinstance(item, str) for item in aliases):
            raise typer.BadParameter(f"markers.{key}.aliases must contain only strings.")
        if feature_types is not None and not isinstance(feature_types, list):
            raise typer.BadParameter(f"markers.{key}.feature_types must be a list of strings.")
        if feature_types is not None and any(not isinstance(item, str) for item in feature_types):
            raise typer.BadParameter(f"markers.{key}.feature_types must contain only strings.")
        if feature_fields is not None and not isinstance(feature_fields, list):
            raise typer.BadParameter(f"markers.{key}.feature_fields must be a list of strings.")
        if feature_fields is not None and any(not isinstance(item, str) for item in feature_fields):
            raise typer.BadParameter(f"markers.{key}.feature_fields must contain only strings.")
        if bold_cfg and not isinstance(bold_cfg, dict):
            raise typer.BadParameter(f"markers.{key}.bold must be a table (dict).")
        marker_codes = bold_cfg.get("marker_codes") if isinstance(bold_cfg, dict) else None
        if marker_codes is None:
            marker_codes = []
        if not isinstance(marker_codes, list):
            raise typer.BadParameter(f"markers.{key}.bold.marker_codes must be a list of strings.")
        if any(not isinstance(item, str) for item in marker_codes):
            raise typer.BadParameter(f"markers.{key}.bold.marker_codes must contain only strings.")
        if uses_ncbi and not phrases and not terms:
            raise typer.BadParameter(f"markers.{key} must define phrases or terms when source uses NCBI.")
        if uses_bold and not str(key).strip():
            raise typer.BadParameter(
                f"markers.{key} must define bold.marker_codes, aliases, phrases, terms, or marker key fallback when source uses BOLD."
            )
        marker_map[key] = {
            "phrases": phrases,
            "terms": terms,
            "aliases": aliases,
            "region_patterns": region_patterns,
            "header_format": header_format,
            "feature_types": feature_types,
            "feature_fields": feature_fields,
            "bold": {"marker_codes": marker_codes},
        }
    return marker_map


def resolve_marker_key(value: str, marker_map: Dict[str, Dict]) -> str:
    value_l = value.lower()
    exact_matches = []
    prefix_matches = []

    for key, cfg in marker_map.items():
        key_l = key.lower()
        aliases = [a.lower() for a in cfg.get("aliases", [])]
        if value_l == key_l or value_l in aliases:
            exact_matches.append(key)
            continue
        if key_l.startswith(value_l) or any(a.startswith(value_l) for a in aliases):
            prefix_matches.append(key)

    if exact_matches:
        if len(exact_matches) > 1:
            raise typer.BadParameter(f"Marker '{value}' matches multiple entries: {exact_matches}")
        return exact_matches[0]
    if prefix_matches:
        if len(prefix_matches) > 1:
            raise typer.BadParameter(f"Marker '{value}' matches multiple entries: {prefix_matches}")
        return prefix_matches[0]

    raise typer.BadParameter(f"Marker '{value}' not found in config.")


def is_raw_term(value: str) -> bool:
    return "[" in value and "]" in value


def build_marker_query(marker_keys: List[str], marker_map: Dict[str, Dict]) -> str:
    phrase_terms: List[str] = []
    for key in marker_keys:
        for term in marker_map[key].get("terms", []):
            phrase_terms.append(term)
        for phrase in marker_map[key].get("phrases", []):
            if is_raw_term(phrase):
                phrase_terms.append(phrase)
            else:
                phrase_escaped = phrase.replace('"', '\\"')
                phrase_terms.append(f'"{phrase_escaped}"[All Fields]')
    if not phrase_terms:
        raise typer.BadParameter("No marker phrases resolved.")
    if len(phrase_terms) == 1:
        return phrase_terms[0]
    return "(" + " OR ".join(f"({t})" for t in phrase_terms) + ")"


def strip_field_spec(value: str) -> str:
    stripped = re.sub(r"\s*\[[^\]]+\]", "", value)
    return stripped.replace('"', "").strip()


def build_region_patterns(marker_cfg: Dict) -> List[str]:
    patterns = marker_cfg.get("region_patterns") or []
    if patterns:
        return patterns

    raw = []
    raw.extend(marker_cfg.get("terms", []))
    raw.extend(marker_cfg.get("phrases", []))
    cleaned = []
    for item in raw:
        stripped = strip_field_spec(item)
        if stripped:
            cleaned.append(re.escape(stripped))
    return cleaned


def compile_patterns(patterns: List[str]) -> List[re.Pattern]:
    compiled = []
    for pat in patterns:
        if not pat:
            continue
        compiled.append(re.compile(pat, re.IGNORECASE))
    return compiled





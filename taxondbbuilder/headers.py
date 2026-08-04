"""Header formatting and extraction helpers."""

import re
from dataclasses import dataclass
from string import Formatter
from typing import Dict, Iterable, List, Optional, Tuple

import typer

from .models import DEFAULT_HEADER_FORMAT

FORMATTER = Formatter()

def sanitize_header(text: str) -> str:
    safe = re.sub(r"\s+", "_", text.strip())
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", safe)
    return safe


def resolve_header_format(cfg: Dict, output_cfg: Dict) -> str:
    header_formats = output_cfg.get("header_formats") or {}
    if not isinstance(header_formats, dict):
        raise typer.BadParameter("[output].header_formats must be a table (dict).")
    default_format = output_cfg.get("default_header_format", DEFAULT_HEADER_FORMAT)
    if not isinstance(default_format, str):
        raise typer.BadParameter("[output].default_header_format must be a string.")
    header_key = cfg.get("header_format")
    if not header_key:
        return default_format
    if not isinstance(header_key, str):
        raise typer.BadParameter("markers.<id>.header_format must be a string.")
    if header_key in header_formats:
        value = header_formats[header_key]
        if not isinstance(value, str):
            raise typer.BadParameter(f"header_formats.{header_key} must be a string.")
        return value
    return header_key


class SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def build_header(template: str, values: Dict[str, str]) -> str:
    return template.format_map(SafeFormatDict(values))


def template_has_field(template: str, field_name: str) -> bool:
    for _, parsed_field, _, _ in FORMATTER.parse(template):
        if parsed_field == field_name:
            return True
    return False


@dataclass(frozen=True)
class HeaderExtractor:
    pattern: re.Pattern
    captures_organism: bool


def compile_header_extractors(header_formats: Iterable[str]) -> Tuple[List[HeaderExtractor], bool, bool]:
    extractors: List[HeaderExtractor] = []
    has_acc_id_template = False
    has_organism_template = False
    for template in sorted(set(header_formats)):
        has_acc_id = template_has_field(template, "acc_id")
        organism_field = None
        if template_has_field(template, "organism_raw"):
            organism_field = "organism_raw"
        elif template_has_field(template, "organism"):
            organism_field = "organism"

        has_acc_id_template = has_acc_id_template or has_acc_id
        has_organism_template = has_organism_template or organism_field is not None
        if not has_acc_id:
            continue

        parts: List[str] = []
        seen_acc_id = False
        seen_organism = False
        for literal, field, _, _ in FORMATTER.parse(template):
            parts.append(re.escape(literal))
            if field is None:
                continue
            if field == "acc_id":
                if not seen_acc_id:
                    parts.append(r"(?P<acc_id>[A-Za-z0-9._-]+)")
                    seen_acc_id = True
                else:
                    parts.append(r"(?P=acc_id)")
            elif organism_field and field == organism_field:
                if not seen_organism:
                    parts.append(r"(?P<organism_name>.*?)")
                    seen_organism = True
                else:
                    parts.append(r"(?P=organism_name)")
            else:
                parts.append(r".*?")
        if seen_acc_id:
            extractors.append(
                HeaderExtractor(
                    pattern=re.compile("^" + "".join(parts) + "$"),
                    captures_organism=seen_organism,
                )
            )
    return extractors, has_acc_id_template, has_organism_template


def extract_header_fields_from_header(header: str, extractors: List[HeaderExtractor]) -> Tuple[Optional[str], Optional[str]]:
    for extractor in extractors:
        match = extractor.pattern.match(header)
        if not match:
            continue
        acc_id = match.groupdict().get("acc_id")
        if not acc_id:
            continue
        organism_name = match.groupdict().get("organism_name")
        if organism_name is not None:
            organism_name = organism_name.strip()
        return acc_id, organism_name or None
    return None, None




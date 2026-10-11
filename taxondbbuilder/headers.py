"""Header formatting and extraction helpers."""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from string import Formatter

import typer

from .models import DEFAULT_HEADER_FORMAT

FORMATTER = Formatter()


def sanitize_header(text: str) -> str:
    safe = re.sub(r"\s+", "_", text.strip())
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", safe)
    return safe


def resolve_header_format(cfg: dict, output_cfg: dict) -> str:
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


def build_header(template: str, values: dict[str, str]) -> str:
    return template.format_map(defaultdict(str, values))


def template_has_field(template: str, field_name: str) -> bool:
    for _, parsed_field, _, _ in FORMATTER.parse(template):
        if parsed_field == field_name:
            return True
    return False


@dataclass(frozen=True)
class HeaderExtractor:
    pattern: re.Pattern
    captures_organism: bool


def _organism_field(template: str) -> str | None:
    """The organism placeholder a template exposes, preferring the raw form."""
    if template_has_field(template, "organism_raw"):
        return "organism_raw"
    if template_has_field(template, "organism"):
        return "organism"
    return None


def _extractor_pattern(template: str, organism_field: str | None):
    """Anchor a template so acc_id and the organism can be read back out.

    Repeated fields become backreferences, so a header only matches when both
    occurrences carry the same value. Returns None without an acc_id field.
    """
    parts: list[str] = []
    seen_acc_id = False
    seen_organism = False
    for literal, field, _, _ in FORMATTER.parse(template):
        parts.append(re.escape(literal))
        if field is None:
            continue
        if field == "acc_id":
            parts.append(r"(?P<acc_id>[A-Za-z0-9._-]+)" if not seen_acc_id else r"(?P=acc_id)")
            seen_acc_id = True
        elif organism_field and field == organism_field:
            parts.append(r"(?P<organism_name>.*?)" if not seen_organism else r"(?P=organism_name)")
            seen_organism = True
        else:
            parts.append(r".*?")
    if not seen_acc_id:
        return None
    return re.compile("^" + "".join(parts) + "$"), seen_organism


def compile_header_extractors(
    header_formats: Iterable[str],
) -> tuple[list[HeaderExtractor], bool, bool]:
    extractors: list[HeaderExtractor] = []
    has_acc_id_template = False
    has_organism_template = False
    for template in sorted(set(header_formats)):
        organism_field = _organism_field(template)
        has_acc_id_template = has_acc_id_template or template_has_field(template, "acc_id")
        has_organism_template = has_organism_template or organism_field is not None
        compiled = _extractor_pattern(template, organism_field)
        if compiled is not None:
            pattern, captures_organism = compiled
            extractors.append(
                HeaderExtractor(pattern=pattern, captures_organism=captures_organism)
            )
    return extractors, has_acc_id_template, has_organism_template


def extract_header_fields_from_header(
    header: str, extractors: list[HeaderExtractor]
) -> tuple[str | None, str | None]:
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

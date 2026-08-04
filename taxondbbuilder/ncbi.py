"""NCBI Entrez, taxonomy, and GenBank helpers."""

import io
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from http import HTTPStatus
from http.client import HTTPException, RemoteDisconnected
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError

import typer
from Bio import Entrez, SeqIO
from rich.progress import Progress

from .console import console
from .headers import sanitize_header
from .models import BuildSource, CanonicalRecord, DEFAULT_HEADER_FORMAT, ResolvedTaxon

@dataclass
class _MatchedNCBIFeature:
    sequence: str
    label: str
    marker: str
    feature_type: str
    header_format: Optional[str]
    start: int
    end: int
    strand: int


def _dump_genbank_record(record: Any, acc: str, taxid: str, dump_gb_dir: Optional[Path], lock: Lock) -> None:
    if not dump_gb_dir:
        return
    gb_root = dump_gb_dir / f"taxid{taxid}"
    gb_root.mkdir(parents=True, exist_ok=True)
    gb_path = gb_root / f"{sanitize_header(acc)}.gb"
    with lock:
        if not gb_path.exists():
            with gb_path.open("w", encoding="utf-8") as f:
                SeqIO.write(record, f, "genbank")


def _match_ncbi_feature(record: Any, feature: Any, marker_rules: List[Dict]) -> Optional[_MatchedNCBIFeature]:
    matched_label = None
    matched_marker = None
    header_format = None
    for rule in marker_rules:
        if rule["feature_types"] and feature.type not in rule["feature_types"]:
            continue
        matched_label = match_feature(feature, rule["patterns"], rule["feature_fields"])
        if matched_label:
            matched_marker = rule["key"]
            header_format = rule["header_format"]
            break
    if not matched_label or not feature.location:
        return None
    try:
        sequence = str(feature.extract(record.seq)).upper()
    except Exception:
        return None
    if not sequence:
        return None
    return _MatchedNCBIFeature(
        sequence=sequence,
        label=str(matched_label),
        marker=str(matched_marker or ""),
        feature_type=str(feature.type),
        header_format=header_format,
        start=int(feature.location.start) + 1,
        end=int(feature.location.end),
        strand=feature.location.strand or 0,
    )


def _build_ncbi_canonical_record(
    acc: str,
    organism: str,
    organism_safe: str,
    matched: _MatchedNCBIFeature,
    dup_index: Optional[int],
    source: BuildSource,
) -> CanonicalRecord:
    acc_id = f"{acc}_dup{dup_index}" if dup_index else acc
    dup_tag = f"dup{dup_index}" if dup_index else ""
    label_safe = sanitize_header(matched.label)
    marker_safe = sanitize_header(matched.marker or "marker")
    type_safe = sanitize_header(matched.feature_type)
    loc = f"{matched.start}-{matched.end}"
    header_values = {
        "acc": acc,
        "acc_id": acc_id,
        "db": "gb",
        "organism": organism_safe,
        "organism_raw": organism,
        "marker": marker_safe,
        "marker_raw": matched.marker,
        "label": label_safe,
        "label_raw": matched.label,
        "type": type_safe,
        "type_raw": matched.feature_type,
        "start": str(matched.start),
        "end": str(matched.end),
        "loc": loc,
        "strand": str(matched.strand),
        "dup": dup_tag,
        "source": source.value,
        "source_id": acc,
    }
    return CanonicalRecord(
        source=source.value,
        source_record_id=acc,
        accession=acc,
        processid=None,
        sampleid=None,
        taxon_name=organism,
        marker_key=matched.marker,
        marker_label=matched.label,
        sequence=matched.sequence,
        header_values=header_values,
        metadata={
            "organism_name": organism,
            "matched_type": matched.feature_type,
            "header_format": matched.header_format or DEFAULT_HEADER_FORMAT,
        },
    )


def _append_ncbi_feature_record(
    acc: str,
    organism: str,
    organism_safe: str,
    matched: _MatchedNCBIFeature,
    source: BuildSource,
    acc_to_seqs: Dict[str, set],
    counters: Dict[str, int],
    dup_accessions: Dict[str, int],
    lock: Lock,
    extracted_records: List[CanonicalRecord],
) -> None:
    with lock:
        counters["matched_features"] += 1
        seqs = acc_to_seqs.setdefault(acc, set())
        if matched.sequence in seqs:
            counters["skipped_same"] += 1
            return
        dup_index = None
        if seqs:
            dup_index = len(seqs) + 1
            counters["duplicated_diff"] += 1
            dup_accessions[acc] = dup_accessions.get(acc, 1) + 1
        seqs.add(matched.sequence)
        extracted_records.append(
            _build_ncbi_canonical_record(acc, organism, organism_safe, matched, dup_index, source)
        )


def _extract_ncbi_record(
    record: Any,
    marker_rules: List[Dict],
    acc_to_seqs: Dict[str, set],
    counters: Dict[str, int],
    dup_accessions: Dict[str, int],
    lock: Lock,
    taxid: str,
    dump_gb_dir: Optional[Path],
    source: BuildSource,
) -> List[CanonicalRecord]:
    if not record.seq:
        return []
    acc = record.id
    organism = str(record.annotations.get("organism", "unknown"))
    organism_safe = sanitize_header(organism)
    _dump_genbank_record(record, acc, taxid, dump_gb_dir, lock)
    extracted_records: List[CanonicalRecord] = []
    record_matched = False
    for feature in record.features:
        matched = _match_ncbi_feature(record, feature, marker_rules)
        if matched is None:
            continue
        record_matched = True
        _append_ncbi_feature_record(
            acc,
            organism,
            organism_safe,
            matched,
            source,
            acc_to_seqs,
            counters,
            dup_accessions,
            lock,
            extracted_records,
        )
    if record_matched:
        with lock:
            counters["matched_records"] += 1
    return extracted_records


def extract_ncbi_records_from_genbank_chunk(
    chunk: str,
    marker_rules: List[Dict],
    acc_to_seqs: Dict[str, set],
    counters: Dict[str, int],
    dup_accessions: Dict[str, int],
    lock: Lock,
    progress: Progress,
    task_id: int,
    taxid: str,
    dump_gb_dir: Optional[Path],
    source: BuildSource = BuildSource.NCBI,
) -> List[CanonicalRecord]:
    extracted_records: List[CanonicalRecord] = []
    handle = io.StringIO(chunk)
    for record in SeqIO.parse(handle, "genbank"):
        with lock:
            counters["total_records"] += 1
        progress.update(task_id, advance=1)
        extracted_records.extend(
            _extract_ncbi_record(
                record,
                marker_rules,
                acc_to_seqs,
                counters,
                dup_accessions,
                lock,
                taxid,
                dump_gb_dir,
                source,
            )
        )
    return extracted_records




def setup_entrez(ncbi_cfg: Dict, warn_if_missing: bool = True) -> None:
    email = ncbi_cfg.get("email") or os.environ.get("NCBI_EMAIL")
    api_key = ncbi_cfg.get("api_key") or os.environ.get("NCBI_API_KEY")

    if not email and warn_if_missing:
        console.print("[yellow]WARNING:[/yellow] NCBI email is not set. Set ncbi.email or NCBI_EMAIL.")
    Entrez.email = email or ""

    if api_key:
        Entrez.api_key = api_key



def feature_texts(feature, fields: List[str]) -> List[str]:
    texts: List[str] = []
    for field in fields:
        val = feature.qualifiers.get(field)
        if not val:
            continue
        if isinstance(val, list):
            texts.extend([str(v) for v in val])
        else:
            texts.append(str(val))
    return texts


def match_feature(feature, compiled_patterns: List[re.Pattern], fields: List[str]) -> Optional[str]:
    texts = feature_texts(feature, fields)
    for text in texts:
        for pat in compiled_patterns:
            if pat.search(text):
                return text
    return None


def build_filter_terms(filters: Dict) -> List[str]:
    def as_str_list(value: object, name: str) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return value
        raise typer.BadParameter(f"filters.{name} must be a string or list of strings.")

    def as_date_str(value: object, name: str) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (date, datetime)):
            return value.strftime("%Y/%m/%d")
        raise typer.BadParameter(f"filters.{name} must be a string or date.")

    terms: List[str] = []
    if filters is None:
        return terms
    if not isinstance(filters, dict):
        raise typer.BadParameter("[filters] must be a table (dict).")
    if not filters:
        return terms

    filters_list = as_str_list(filters.get("filter"), "filter")
    for value in filters_list:
        terms.append(f"{value}[filter]")

    properties_list = as_str_list(filters.get("properties"), "properties")
    for value in properties_list:
        terms.append(f"{value}[prop]")

    length_min = filters.get("sequence_length_min")
    length_max = filters.get("sequence_length_max")
    if length_min is not None or length_max is not None:
        try:
            lmin = int(length_min) if length_min is not None else 0
            lmax = int(length_max) if length_max is not None else 1000000000
        except (TypeError, ValueError) as exc:
            raise typer.BadParameter("filters.sequence_length_min/max must be integers.") from exc
        terms.append(f"{lmin}[SLEN] : {lmax}[SLEN]")

    date_from = as_date_str(filters.get("publication_date_from"), "publication_date_from")
    date_to = as_date_str(filters.get("publication_date_to"), "publication_date_to")
    if date_from or date_to:
        dfrom = date_from or "1800/01/01"
        dto = date_to or "3000/12/31"
        terms.append(f"{dfrom}[PDAT] : {dto}[PDAT]")

    date_from = as_date_str(filters.get("modification_date_from"), "modification_date_from")
    date_to = as_date_str(filters.get("modification_date_to"), "modification_date_to")
    if date_from or date_to:
        dfrom = date_from or "1800/01/01"
        dto = date_to or "3000/12/31"
        terms.append(f"{dfrom}[MDAT] : {dto}[MDAT]")

    include_keywords = as_str_list(filters.get("all_fields_include"), "all_fields_include")
    if include_keywords:
        inc = [f'"{k}"[All Fields]' for k in include_keywords]
        terms.append("(" + " OR ".join(inc) + ")")

    exclude_keywords = as_str_list(filters.get("all_fields_exclude"), "all_fields_exclude")
    if exclude_keywords:
        exc = [f'"{k}"[All Fields]' for k in exclude_keywords]
        terms.append("NOT (" + " OR ".join(exc) + ")")

    raw = filters.get("raw")
    if raw is not None:
        raw_terms = as_str_list(raw, "raw")
        for term in raw_terms:
            terms.append(str(term))

    return terms


def build_query(taxid: str, marker_query: str, filters: Dict, taxon_noexp: bool) -> str:
    tax_term = f"txid{taxid}[Organism:noexp]" if taxon_noexp else f"txid{taxid}[Organism]"
    parts = [tax_term, marker_query]
    parts.extend(build_filter_terms(filters))
    return " AND ".join(f"({p})" for p in parts)


def fetch_taxonomy_scientific_name(taxid: str) -> str:
    handle = Entrez.efetch(db="taxonomy", id=taxid, retmode="xml")
    record = Entrez.read(handle)

    if isinstance(record, list) and record:
        scientific_name = record[0].get("ScientificName")
        if scientific_name:
            return str(scientific_name)
    if isinstance(record, dict):
        scientific_name = record.get("ScientificName")
        if scientific_name:
            return str(scientific_name)

    raise typer.BadParameter(f"Could not resolve scientific name for taxid {taxid}.")


def resolve_taxon(taxon: str, require_scientific_name: bool = False) -> ResolvedTaxon:
    warning = None
    if re.fullmatch(r"\d+", taxon):
        taxid = taxon
        scientific_name = fetch_taxonomy_scientific_name(taxid) if require_scientific_name else taxon
    else:
        term = f'"{taxon}"[Scientific Name]'
        handle = Entrez.esearch(db="taxonomy", term=term, retmax=5)
        record = Entrez.read(handle)
        ids = record.get("IdList", [])
        if not ids:
            raise typer.BadParameter(f"Taxon not found in NCBI Taxonomy: {taxon}")

        taxid = ids[0]
        if len(ids) > 1:
            warning = f"Multiple taxids found for '{taxon}'. Using {taxid}."
        scientific_name = fetch_taxonomy_scientific_name(taxid) if require_scientific_name else taxon
    return ResolvedTaxon(
        input_value=taxon,
        taxid=taxid,
        scientific_name=scientific_name,
        warning=warning,
    )


def default_delay(ncbi_cfg: Dict) -> float:
    delay = ncbi_cfg.get("delay_sec")
    if delay is not None:
        return float(delay)
    if getattr(Entrez, "api_key", None):
        return 0.11
    return 0.34


@dataclass
class _GenbankFetchContext:
    query: str
    db: str
    rettype: str
    retmode: str
    per_query: int
    use_history: bool
    fetch_retries: int
    retry_delay_sec: float
    delay_sec: float
    start_at: int
    resume: bool
    count: int
    webenv: Any
    query_key: Any
    cache_root: Optional[Path]


def _create_genbank_fetch_context(
    query: str,
    ncbi_cfg: Dict,
    delay_sec: float,
    start_at: int = 0,
    dump_dir: Optional[Path] = None,
    resume: bool = False,
    taxid: Optional[str] = None,
) -> _GenbankFetchContext:
    db = ncbi_cfg.get("db", "nucleotide")
    rettype = ncbi_cfg.get("rettype", "fasta")
    retmode = ncbi_cfg.get("retmode", "text")
    per_query = int(ncbi_cfg.get("per_query", 100))
    use_history = bool(ncbi_cfg.get("use_history", True))
    fetch_retries = max(1, int(ncbi_cfg.get("fetch_retries", 4)))
    retry_delay_sec = float(ncbi_cfg.get("retry_delay_sec", max(delay_sec, 0.5)))

    if rettype not in {"gb", "gbwithparts"}:
        raise typer.BadParameter("ncbi.rettype must be 'gb' or 'gbwithparts' for region extraction.")
    handle = Entrez.esearch(db=db, term=query, retmax=0, usehistory="y" if use_history else "n")
    record = Entrez.read(handle)
    count = int(record.get("Count", 0))
    webenv = record.get("WebEnv")
    query_key = record.get("QueryKey")
    cache_root = None
    if dump_dir:
        cache_root = dump_dir / ".cache"
        cache_root.mkdir(parents=True, exist_ok=True)
    return _GenbankFetchContext(
        query=query,
        db=db,
        rettype=rettype,
        retmode=retmode,
        per_query=per_query,
        use_history=use_history,
        fetch_retries=fetch_retries,
        retry_delay_sec=retry_delay_sec,
        delay_sec=delay_sec,
        start_at=start_at,
        resume=resume,
        count=count,
        webenv=webenv,
        query_key=query_key,
        cache_root=cache_root,
    )


def _genbank_cache_path(ctx: _GenbankFetchContext, start: int) -> Optional[Path]:
    if not ctx.cache_root:
        return None
    return ctx.cache_root / f"start{start:09d}_count{ctx.per_query:04d}.cache"


def _load_genbank_cache(ctx: _GenbankFetchContext, start: int) -> Optional[str]:
    path = _genbank_cache_path(ctx, start)
    if path and path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")
    return None


def _save_genbank_cache(ctx: _GenbankFetchContext, start: int, data: str) -> None:
    path = _genbank_cache_path(ctx, start)
    if path:
        path.write_text(data, encoding="utf-8")


def _read_efetch_with_retries(ctx: _GenbankFetchContext, start: int, **fetch_kwargs: Any) -> str:
    retryable_http_codes = {
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
    }
    last_exc: Optional[Exception] = None
    for attempt in range(1, ctx.fetch_retries + 1):
        try:
            fetch_handle = Entrez.efetch(**fetch_kwargs)
            try:
                return fetch_handle.read()
            finally:
                fetch_handle.close()
        except HTTPError as exc:
            last_exc = exc
            if exc.code == HTTPStatus.BAD_REQUEST:
                raise
            if exc.code not in retryable_http_codes or attempt >= ctx.fetch_retries:
                raise
        except (HTTPException, RemoteDisconnected, URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt >= ctx.fetch_retries:
                raise
        wait_sec = ctx.retry_delay_sec * attempt
        print(
            f"# efetch retry start={start} attempt={attempt}/{ctx.fetch_retries}"
            f" wait={wait_sec:.2f}s reason={last_exc}",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(wait_sec)
    raise RuntimeError("efetch retries exhausted")


def _fetch_genbank_chunk_by_ids(ctx: _GenbankFetchContext, start: int) -> Optional[str]:
    search_handle = Entrez.esearch(
        db=ctx.db,
        term=ctx.query,
        retstart=start,
        retmax=ctx.per_query,
        usehistory="n",
    )
    search_record = Entrez.read(search_handle)
    ids = search_record.get("IdList", [])
    if not ids:
        return None
    return _read_efetch_with_retries(
        ctx,
        start,
        db=ctx.db,
        rettype=ctx.rettype,
        retmode=ctx.retmode,
        id=",".join(ids),
    )


def _iter_genbank_history_chunks(ctx: _GenbankFetchContext) -> Iterable[Tuple[int, str]]:
    for start in range(ctx.start_at, ctx.count, ctx.per_query):
        cached = _load_genbank_cache(ctx, start) if ctx.resume else None
        if cached is not None:
            yield start, cached
            continue
        try:
            data = _read_efetch_with_retries(
                ctx,
                start,
                db=ctx.db,
                rettype=ctx.rettype,
                retmode=ctx.retmode,
                retstart=start,
                retmax=ctx.per_query,
                webenv=ctx.webenv,
                query_key=ctx.query_key,
            )
        except HTTPError as exc:
            if exc.code != HTTPStatus.BAD_REQUEST:
                raise
            data = _fetch_genbank_chunk_by_ids(ctx, start)
            if data is None:
                continue
        _save_genbank_cache(ctx, start, data)
        yield start, data
        time.sleep(ctx.delay_sec)


def _iter_genbank_direct_chunks(ctx: _GenbankFetchContext) -> Iterable[Tuple[int, str]]:
    for start in range(ctx.start_at, ctx.count, ctx.per_query):
        cached = _load_genbank_cache(ctx, start) if ctx.resume else None
        if cached is not None:
            yield start, cached
            continue
        data = _fetch_genbank_chunk_by_ids(ctx, start)
        if data is None:
            continue
        _save_genbank_cache(ctx, start, data)
        yield start, data
        time.sleep(ctx.delay_sec)


def _iter_genbank_chunks(ctx: _GenbankFetchContext) -> Iterable[Tuple[int, str]]:
    if ctx.use_history and ctx.webenv and ctx.query_key:
        yield from _iter_genbank_history_chunks(ctx)
        return
    yield from _iter_genbank_direct_chunks(ctx)


def fetch_genbank(
    query: str,
    ncbi_cfg: Dict,
    delay_sec: float,
    start_at: int = 0,
    dump_dir: Optional[Path] = None,
    resume: bool = False,
    taxid: Optional[str] = None,
) -> Tuple[int, Iterable[Tuple[int, str]]]:
    ctx = _create_genbank_fetch_context(query, ncbi_cfg, delay_sec, start_at, dump_dir, resume, taxid)
    if ctx.count == 0:
        return 0, []
    return ctx.count, _iter_genbank_chunks(ctx)


def iter_genbank_files(from_dir: Path, taxid: Optional[str] = None) -> Iterable[Tuple[int, str]]:
    gb_root = from_dir
    if taxid:
        candidate = from_dir / f"taxid{taxid}"
        if candidate.exists():
            gb_root = candidate
    files = sorted(gb_root.glob("*.gb"))
    for idx, path in enumerate(files):
        yield idx, path.read_text(encoding="utf-8", errors="ignore")


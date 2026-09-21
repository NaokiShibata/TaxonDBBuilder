"""Conservative repair of mitochondrial feature locations from record evidence."""

import re
from bisect import bisect_left
from collections import defaultdict
from copy import deepcopy

from Bio.Data import CodonTable
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, ExactPosition, SeqFeature, SimpleLocation

# Order and strands are for the vertebrate mitochondrial reference orientation.
ORDER = [
    "tRNA-Phe", "12S", "tRNA-Val", "16S", "tRNA-Leu(UUR)", "ND1",
    "tRNA-Ile", "tRNA-Gln", "tRNA-Met", "ND2", "tRNA-Trp", "tRNA-Ala",
    "tRNA-Asn", "tRNA-Cys", "tRNA-Tyr", "COI", "tRNA-Ser(UCN)",
    "tRNA-Asp", "COII", "tRNA-Lys", "ATP8", "ATP6", "COIII", "tRNA-Gly",
    "ND3", "tRNA-Arg", "ND4L", "ND4", "tRNA-His", "tRNA-Ser(AGY)",
    "tRNA-Leu(CUN)", "ND5", "ND6", "tRNA-Glu", "CYTB", "tRNA-Thr",
    "tRNA-Pro", "control_region",
]
NEGATIVE = {"ND6", "tRNA-Gln", "tRNA-Ala", "tRNA-Asn", "tRNA-Cys",
            "tRNA-Tyr", "tRNA-Ser(UCN)", "tRNA-Glu", "tRNA-Pro"}
CDS_REGIONS = {"COI", "COII", "COIII", "CYTB", "ATP6", "ATP8",
               "ND1", "ND2", "ND3", "ND4", "ND4L", "ND5", "ND6"}
# This detects obvious truncation, not the biological minimum length of a gene.
MIN_LENGTH = 10


def _normalize(value):
    return re.sub(r"[\s_-]+", "", value.casefold())


ALIASES = {region: {region} for region in ORDER}
ALIASES.update({
    "12S": {"12S", "rrnS", "12S rRNA", "12S ribosomal RNA", "small subunit ribosomal RNA"},
    "16S": {"16S", "rrnL", "16S rRNA", "16S ribosomal RNA", "large subunit ribosomal RNA"},
    "control_region": {"D-loop", "dloop", "control region"},
    "CYTB": {"CYTB", "cyt b", "cob", "cytochrome b", "MT-CYB"},
})
for numeral, number in [("I", 1), ("II", 2), ("III", 3)]:
    ALIASES[f"CO{numeral}"] |= {f"CO{number}", f"COX{number}", f"MT-CO{number}",
                                f"cytochrome c oxidase subunit {numeral}"}
for region in CDS_REGIONS:
    ALIASES[region].add(f"MT-{region}")
    if region.startswith("ND"):
        ALIASES[region] |= {f"NAD{region[2:]}", f"NADH dehydrogenase subunit {region[2:]}"}
    if region.startswith("ATP"):
        ALIASES[region] |= {f"ATPase subunit {region[3:]}",
                            f"ATP synthase F0 subunit {region[3:]}"}
for amino, letter in zip(
    ["Phe", "Val", "Ile", "Gln", "Met", "Trp", "Ala", "Asn", "Cys", "Tyr",
     "Asp", "Lys", "Gly", "Arg", "His", "Glu", "Thr", "Pro"],
    "FVIQMWANCYDKGRHETP",
):
    ALIASES[f"tRNA-{amino}"] |= {f"trn{letter}", f"MT-T{letter}"}
for region, names in {
    "tRNA-Leu(UUR)": {"trnL1", "tRNA-Leu1", "MT-TL1"},
    "tRNA-Leu(CUN)": {"trnL2", "tRNA-Leu2", "MT-TL2"},
    # trnS numbering varies across sources; require the codon group instead.
    "tRNA-Ser(UCN)": {"MT-TS2"},
    "tRNA-Ser(AGY)": {"MT-TS1"},
}.items():
    ALIASES[region] |= names
ALIAS_IDS = {_normalize(name): region for region, names in ALIASES.items() for name in names}
NOTE_PATTERNS = [(re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])", re.I), region)
                 for region, names in ALIASES.items() for name in names]


def identify_region(feature):
    """Return only unambiguous identities; short substrings are never matches."""
    if feature.type not in {"gene", "CDS", "rRNA", "tRNA", "D-loop", "misc_feature",
                            "repeat_region", "rep_origin"}:
        return None
    found = {"control_region"} if feature.type == "D-loop" else set()
    for field in ("gene", "product", "gene_synonym", "standard_name"):
        for value in feature.qualifiers.get(field, []):
            for name in value.split(";"):
                region = ALIAS_IDS.get(_normalize(name))
                if region:
                    found.add(region)
    if not found:
        labels = {_normalize(v) for field in ("product", "gene")
                  for v in feature.qualifiers.get(field, [])}
        anticodons = " ".join(feature.qualifiers.get("anticodon", []) + feature.qualifiers.get("note", [])).upper()
        for amino, codon, region in [("Leu", "TAA", "tRNA-Leu(UUR)"), ("Leu", "TAG", "tRNA-Leu(CUN)"),
                                    ("Ser", "TGA", "tRNA-Ser(UCN)"), ("Ser", "GCT", "tRNA-Ser(AGY)")]:
            if _normalize("tRNA-" + amino) in labels and re.search(
                    r"(?:SEQ:|ANTICODON:\s*)" + codon + r"(?![ACGT])", anticodons.replace("U", "T")):
                found.add(region)
    if not found:
        for note in feature.qualifiers.get("note", []):
            found.update(region for pattern, region in NOTE_PATTERNS if pattern.search(note))
    return next(iter(found)) if len(found) == 1 else None


def location_text(location):
    """Serialize supported locations in GenBank coordinates without private APIs."""
    if location is None:
        return "unknown"
    def part_text(part):
        def position(pos, start=False):
            raw = str(pos)
            prefix = raw[0] if raw.startswith(("<", ">")) else ""
            return prefix + str(int(pos) + int(start))
        start, end = position(part.start, True), position(part.end)
        return start if start == end else f"{start}..{end}"
    try:
        parts = list(location.parts)
        if location.strand == -1:
            parts.reverse()
        text = ",".join(part_text(part) for part in parts)
        if len(parts) > 1:
            text = f"join({text})"
        return f"complement({text})" if location.strand == -1 else text
    except (ValueError, TypeError):
        return str(location)


def _valid_location(location, length, exact=False):
    if location is None:
        return False
    try:
        return all(
            not part.ref and not part.ref_db and 0 <= int(part.start) < int(part.end) <= length
            and (not exact or (type(part.start) is ExactPosition and type(part.end) is ExactPosition))
            for part in location.parts
        )
    except (ValueError, TypeError):
        return False


def _issue(feature, record):
    loc = feature.location
    if not _valid_location(loc, len(record)):
        return "invalid_location"
    try:
        bases = str(feature.extract(record.seq))
    except (ValueError, TypeError):
        return "extraction_failed"
    if len(bases) <= MIN_LENGTH:
        return "single_base_location" if len(bases) == 1 else "short_location"
    if not _valid_location(loc, len(record), exact=True) or "partial" in feature.qualifiers:
        return "partial_location"
    # Ordinary valid annotations retain their interpretation of transl_except.
    if feature.type == "CDS" and feature.qualifiers.get("translation") and not feature.qualifiers.get("transl_except"):
        try:
            offset = int(feature.qualifiers.get("codon_start", ["1"])[0]) - 1
            table = _translation_table(record, [feature])
            coding = bases[offset:]
            protein = str(Seq(coding[:len(coding) // 3 * 3]).translate(table=table)).rstrip("*")
            expected = "".join(feature.qualifiers["translation"][0].split())
            if protein and expected and protein[1:] != expected[1:]:
                return "translation_mismatch"
        except (ValueError, KeyError, TypeError):
            pass
    return None


def _translation_table(record, features):
    tables = {int(value) for f in features for value in f.qualifiers.get("transl_table", [])}
    if not tables and "Vertebrata" in record.annotations.get("taxonomy", []):
        tables = {2}
    if len(tables) != 1 or next(iter(tables)) not in CodonTable.unambiguous_dna_by_id:
        raise ValueError("unknown_translation_table")
    return next(iter(tables))


def _arc(start, end, length, circular, strand=1):
    if start == end or (end < start and not circular):
        raise ValueError("empty_interval" if start == end else "unsupported_topology")
    parts = ([SimpleLocation(start, end, strand=1)] if start < end else
             [SimpleLocation(a, b, strand=1) for a, b in [(start, length), (0, end)] if a < b])
    if strand == -1:
        parts = [SimpleLocation(p.start, p.end, strand=-1) for p in reversed(parts)]
    return parts[0] if len(parts) == 1 else CompoundLocation(parts)


def _overlaps(a, b):
    return any(int(x.start) < int(y.end) and int(y.start) < int(x.end)
               for x in a.parts for y in b.parts)


def _compatible(location, originals, length):
    points = set(location)
    for f in originals:
        loc = f.location
        if not _valid_location(loc, length):
            continue
        if not set(loc) <= points or (loc.strand == -1 and location.strand != -1):
            return False
        if not _valid_location(loc, length, exact=True):
            if type(loc.start) is ExactPosition and int(loc.start) != int(location.start):
                return False
            if type(loc.end) is ExactPosition and int(loc.end) != int(location.end):
                return False
    return True


def _flanks(record, region, index):
    if not any("mitochondri" in value.casefold() for f in record.features if f.type == "source"
               for value in f.qualifiers.get("organelle", [])):
        raise ValueError("unsupported_context")
    if "Vertebrata" not in record.annotations.get("taxonomy", []):
        raise ValueError("unsupported_context")
    i = ORDER.index(region)
    left_id, right_id = ORDER[i - 1], ORDER[(i + 1) % len(ORDER)]
    anchors = []
    for name in (left_id, right_id):
        usable = {str(f.location): f for f in index[name]
                  if _valid_location(f.location, len(record), exact=True)
                  and isinstance(f.location, SimpleLocation) and len(f.location) > MIN_LENGTH
                  and f.location.strand in {-1, 1} and _issue(f, record) is None}
        if not usable:
            raise ValueError("missing_flank")
        if len(usable) != 1:
            raise ValueError("ambiguous_flanks")
        anchors.append(next(iter(usable.values())))
    left, right = anchors
    if _overlaps(left.location, right.location):
        raise ValueError("overlapping_flanks")
    orientation = left.location.strand * (-1 if left_id in NEGATIVE else 1)
    if right.location.strand != orientation * (-1 if right_id in NEGATIVE else 1):
        raise ValueError("unexpected_strands")
    circular = record.annotations.get("topology", "").casefold() == "circular"
    a, b = (left, right) if orientation == 1 else (right, left)
    strand = orientation * (-1 if region in NEGATIVE else 1)
    gap = _arc(int(a.location.end), int(b.location.start), len(record), circular, strand)
    # CDS can extend into either flanking annotation; the gap is not its boundary.
    window = _arc(int(a.location.start), int(b.location.end), len(record), circular, strand)
    for feature in record.features:
        if feature.type not in {"gene", "CDS", "rRNA", "tRNA"}:
            continue
        if identify_region(feature) in {region, left_id, right_id}:
            continue
        if _valid_location(feature.location, len(record)) and _overlaps(gap, feature.location):
            raise ValueError("intervening_gene")
    metadata = {"profile": "vertebrate", "left_flank_id": left_id,
                "right_flank_id": right_id, "left_flank_location": location_text(left.location),
                "right_flank_location": location_text(right.location)}
    return gap, window, metadata, anchors


def _slice_location(location, start, end):
    """Slice in extraction order, including reverse strands and circular joins."""
    parts = []
    offset = 0
    for part in location.parts:
        a, b = max(start - offset, 0), min(end - offset, len(part))
        if a < b:
            if part.strand == -1:
                parts.append(SimpleLocation(int(part.end) - b, int(part.end) - a, strand=-1))
            else:
                parts.append(SimpleLocation(int(part.start) + a, int(part.start) + b, strand=1))
        offset += len(part)
    return parts[0] if len(parts) == 1 else CompoundLocation(parts)


def _infer_cds(record, originals, gap, window, anchors):
    terminal_exceptions = []
    for feature in originals:
        for value in feature.qualifiers.get("transl_except", []):
            match = re.fullmatch(r"\(pos:(?:complement\()?([0-9]+)(?:\.\.([0-9]+))?\)?,aa:TERM\)",
                                 re.sub(r"\s+", "", value))
            if not match:
                raise ValueError("unsupported_translation_exception")
            start, end = int(match[1]) - 1, int(match[2] or match[1])
            if end - start not in (1, 2) or not 0 <= start < end <= len(record):
                raise ValueError("unsupported_translation_exception")
            terminal_exceptions.append(set(range(start, end)))
    if any(f.qualifiers.get("codon_start", ["1"])[0] != "1" for f in originals):
        raise ValueError("unsupported_codon_start")
    table_id = _translation_table(record, originals)
    table = CodonTable.unambiguous_dna_by_id[table_id]
    proteins = {"".join(value.split()).upper() for f in originals
                for value in f.qualifiers.get("translation", [])}
    if len(proteins) > 1:
        raise ValueError("conflicting_translation")
    expected = next(iter(proteins), None)
    bases = str(window.extract(record.seq)).upper()
    if set(bases) - set("ACGT"):
        raise ValueError("ambiguous_coding_sequence")
    if not expected and not any(_valid_location(f.location, len(record)) for f in originals):
        raise ValueError("insufficient_cds_evidence")
    # Precompute stops per frame instead of translating every suffix of the window.
    stops = {frame: [i for i in range(frame, len(bases) - 2, 3)
                     if bases[i:i + 3] in table.stop_codons] for frame in range(3)}
    window_points = list(window)
    gap_points = set(gap)
    gap_end = max(i for i, p in enumerate(window_points) if p in gap_points) + 1
    terminal_anchor = next((f for f in anchors if gap_end < len(window_points)
                            and window_points[gap_end] in f.location), None)
    allow_partial_stop = (terminal_anchor is not None
                          and terminal_anchor.type in {"tRNA", "rRNA"}
                          and terminal_anchor.location.strand == gap.strand)
    candidates = {}
    for start in range(len(bases) - MIN_LENGTH):
        if bases[start:start + 3] not in table.start_codons:
            continue
        frame_stops = stops[start % 3]
        stop_idx = bisect_left(frame_stops, start)
        end = frame_stops[stop_idx] + 3 if stop_idx < len(frame_stops) else None
        ends = [end] if end is not None else []
        remainder = (gap_end - start) % 3
        if allow_partial_stop and gap_end > start and remainder in (1, 2):
            tail = bases[gap_end - remainder:gap_end]
            if tail in {"T", "TA"} and (end is None or end > gap_end):
                ends.append(gap_end)
        for end in ends:
            if end - start <= MIN_LENGTH:
                continue
            remainder = (end - start) % 3
            if terminal_exceptions and (not remainder or any(
                    set(window_points[end - remainder:end]) != positions for positions in terminal_exceptions)):
                continue
            loc = _slice_location(window, start, end)
            if not _compatible(loc, originals, len(record)):
                continue
            if not expected:
                # A lone point may identify the start, but cannot select an arbitrary ORF.
                known_start = any(_valid_location(f.location, len(record)) and
                                  next(iter(f.location)) == window_points[start] for f in originals)
                if not known_start:
                    continue
            coding = bases[start:end]
            protein = str(Seq(coding[:len(coding) // 3 * 3]).translate(table=table_id)).rstrip("*")
            if "*" in protein or (expected and "M" + protein[1:] != expected):
                continue
            candidates[str(loc)] = loc
    if len(candidates) != 1:
        raise ValueError("ambiguous_cds_boundaries" if candidates else "insufficient_cds_evidence")
    loc = next(iter(candidates.values()))
    overlaps = [len(set(loc) & set(f.location)) for f in anchors]
    return loc, {"transl_table": str(table_id), "overlap_bases": ",".join(map(str, overlaps)),
                 "extraction_method": "translation_guided" if expected else "constrained_cds",
                 "boundary_evidence": "unique_translation_and_stop" if expected else "known_start_and_unique_stop"}


def resolve_regions(record, targets):
    """Return (region, feature, provenance) results and diagnostic events."""
    index = defaultdict(list)
    for feature in record.features:
        region = identify_region(feature)
        if region:
            index[region].append(feature)
    results, events = [], []
    for region in targets:
        originals = index[region]
        good, bad = [], []
        for feature in originals:
            reason = _issue(feature, record)
            (bad if reason else good).append((feature, reason))
        reason = ";".join(sorted({r for _, r in bad})) or "missing_feature"
        original = ";".join(sorted({location_text(f.location) for f, _ in bad})) or "unknown"
        provenance = {"region_id": region, "fallback_reason": reason, "original_location": original,
                      "coordinate_system": "1-based-inclusive", "stage": "extraction_before_post_prep"}
        if region in CDS_REGIONS and bad and good:
            # A gene feature supplies a candidate span, not proof of CDS boundaries.
            validated = []
            for feature, _ in good:
                if feature.type != "gene":
                    validated.append((feature, None))
                    continue
                try:
                    loc, _ = _infer_cds(record, [f for f, _ in bad], feature.location,
                                        feature.location, [])
                    if loc != feature.location:
                        raise ValueError("gene_cds_boundary_mismatch")
                    validated.append((feature, None))
                except (ValueError, TypeError):
                    bad.append((feature, "unverified_gene_boundary"))
            good = validated
        if good:
            unique = {}
            for f, _ in good:
                unique.setdefault(str(f.location), f)
            # Different gene and CDS boundaries at the same locus require review.
            locations = [f.location for f in unique.values()]
            conflict = any(_overlaps(a, b) for i, a in enumerate(locations) for b in locations[i + 1:])
            if conflict:
                events.append({**provenance, "status": "skipped", "reason": "conflicting_annotation"})
                continue
            for feature in unique.values():
                metadata = {}
                if bad and len(unique) == 1 and _compatible(feature.location, [f for f, _ in bad], len(record)):
                    metadata = {**provenance, "status": "recovered", "extraction_method": "alternate_feature",
                                "boundary_evidence": "same_locus_annotation"}
                results.append((region, feature, metadata))
            if bad:
                recovered = next((m for r, f, m in results if r == region and m), None)
                if recovered:
                    chosen = next(f for r, f, m in results if r == region and m)
                    events.append({**recovered, "reason": "alternate_feature",
                                   "inferred_location": location_text(chosen.location),
                                   "inferred_length": str(len(chosen.location)), "strand": str(chosen.location.strand or 0)})
                else:
                    events.append({**provenance, "status": "skipped",
                                   "reason": "conflicting_annotation" if len(unique) == 1 else "ambiguous_copies"})
            continue
        try:
            gap, window, metadata, anchors = _flanks(record, region, index)
            if region in CDS_REGIONS:
                loc, cds_metadata = _infer_cds(record, originals, gap, window, anchors)
                metadata.update(cds_metadata)
            else:
                loc = gap
                # Preserve exact endpoints of a partial, simple annotation.
                for feature in originals:
                    old = feature.location
                    if _valid_location(old, len(record)) and not _valid_location(old, len(record), exact=True):
                        if not isinstance(loc, SimpleLocation):
                            raise ValueError("unsupported_partial_location")
                        loc = SimpleLocation(old.start if type(old.start) is ExactPosition else loc.start,
                                             old.end if type(old.end) is ExactPosition else loc.end,
                                             strand=loc.strand)
                metadata.update(extraction_method="flanking_interval", boundary_evidence="flanking_interval")
            if len(loc) <= MIN_LENGTH or not _compatible(loc, originals, len(record)):
                raise ValueError("conflicting_annotation")
            str(loc.extract(record.seq))  # Reject undefined sequence before recording success.
            kind = "CDS" if region in CDS_REGIONS else "tRNA" if region.startswith("tRNA") else "D-loop" if region == "control_region" else "rRNA"
            feature = SeqFeature(loc, type=kind, qualifiers={"gene": [region]})
            metadata.update(provenance, status="inferred")
            results.append((region, feature, metadata))
            events.append({**metadata, "reason": reason, "inferred_location": location_text(loc), "inferred_length": str(len(loc)), "strand": str(loc.strand or 0)})
        except (ValueError, TypeError) as exc:
            failure = str(exc)
            status = "partial_unresolved" if any(issue == "partial_location" for _, issue in bad) else "skipped"
            events.append({**provenance, "status": status, "reason": failure})
            for feature, issue in bad:
                if issue == "partial_location":
                    results.append((region, deepcopy(feature), {**provenance, "status": "partial_unresolved",
                                    "extraction_method": "original_partial", "boundary_evidence": failure}))
    for region, feature, metadata in results:
        if metadata:
            metadata.update(inferred_location=location_text(feature.location),
                            inferred_length=str(len(feature.location)), strand=str(feature.location.strand or 0))
    return results, events

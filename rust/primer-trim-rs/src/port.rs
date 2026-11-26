//! The Rust port of `taxondbbuilder/postprep/primer_trim.py`.
//!
//! The whole pipeline entry point moves to Rust; the dataclass-returning helpers stay in
//! Python. `Err("Kind: text")` maps back to the same Python exception at the boundary.
//! Path arguments arrive as `{"$path": "..."}` (see the `__fspath__` branch in pyconv.rs);
//! anything else reproduces the source's AttributeError on `.with_suffix`.

#![forbid(unsafe_code)]

use serde_json::{Map, Value, json};
use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};

type Row = Map<String, Value>;

/// A primer with its Python `.upper()` expansion precomputed.
pub struct Primer {
    pub original: String,
    pub upper: String,
}

/// The module-level `run_vsearch_endpoint_recheck` of `primer_trim.py`, passed in by the
/// bindings so monkeypatching it (as the tests do) still takes effect.
pub type RecheckFn<'a> =
    dyn Fn(&Ctx, &mut Vec<Row>, &[String]) -> Result<(i64, i64, Option<String>), String> + 'a;

pub struct Ctx {
    pub forward: Vec<Primer>,
    pub reverse: Vec<Primer>,
    pub forward_rc: Vec<Primer>,
    pub reverse_rc: Vec<Primer>,
    pub recheck_min_identity: f64,
    pub recheck_min_query_cov: f64,
    fasta: PathBuf,
    trim_mode: String,
    max_mismatch: i64,
    max_error_rate: f64,
    min_overlap_bp: Option<i64>,
    min_overlap_ratio: f64,
    end_max_offset: i64,
    keep_retained_fasta: bool,
    iter_enable: bool,
    iter_max_rounds: i64,
    iter_stop_delta: f64,
    iter_target_conf: f64,
    sidecar_format: String,
    recheck_tool: String,
    retained_path: PathBuf,
}

#[derive(Clone, Copy)]
struct Counts {
    trimmed_both: i64,
    trimmed_left_only: i64,
    trimmed_right_only: i64,
    untrimmed: i64,
    dropped_empty: i64,
    canonical_orientation: i64,
    reverse_orientation: i64,
    confidence_high: i64,
    confidence_medium: i64,
    confidence_low: i64,
}

impl Counts {
    fn new() -> Self {
        Counts {
            trimmed_both: 0,
            trimmed_left_only: 0,
            trimmed_right_only: 0,
            untrimmed: 0,
            dropped_empty: 0,
            canonical_orientation: 0,
            reverse_orientation: 0,
            confidence_high: 0,
            confidence_medium: 0,
            confidence_low: 0,
        }
    }
}

struct RoundResult {
    round: i64,
    records: Vec<(String, String)>,
    rows: Vec<Row>,
    counts: Counts,
    high_conf_rate: f64,
}

// ---------- Python-semantics helpers ----------

fn py_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_i64() || n.is_u64() {
                "int"
            } else {
                "float"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(m) => {
            if m.len() == 1 {
                if matches!(m.get("$int"), Some(Value::String(_))) {
                    return "int";
                }
                if marker_float(m).is_some() {
                    return "float";
                }
            }
            "dict"
        }
    }
}

/// Unwrap the recording harness's marker objects: {"$int": "12"}, {"$float": "1.5"}, {"$num": "NaN"}.
fn unwrap_marker(v: &Value) -> Value {
    if let Value::Object(m) = v
        && m.len() == 1
    {
        if let Some(Value::String(s)) = m.get("$int")
            && let Ok(i) = s.parse::<i64>()
        {
            return json!(i);
        }
        if let Some(f) = marker_float(m) {
            return json!(f);
        }
    }
    v.clone()
}

fn marker_float(m: &Map<String, Value>) -> Option<f64> {
    let named = |s: &str| match s {
        "NaN" => Some(f64::NAN),
        "Infinity" => Some(f64::INFINITY),
        "-Infinity" => Some(f64::NEG_INFINITY),
        "-0" => Some(-0.0),
        _ => None,
    };
    if let Some(Value::String(s)) = m.get("$num") {
        return named(s).or_else(|| s.parse::<f64>().ok());
    }
    if let Some(Value::String(s)) = m.get("$float") {
        return s.parse::<f64>().ok();
    }
    None
}

fn opts_get(opts: &Value, key: &str, default: Value) -> Value {
    opts.as_object()
        .and_then(|m| m.get(key))
        .map(unwrap_marker)
        .unwrap_or(default)
}

fn to_int(v: &Value) -> Result<i64, String> {
    let v = unwrap_marker(v);
    match &v {
        Value::Bool(b) => Ok(i64::from(*b)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i)
            } else if let Some(f) = n.as_f64() {
                Ok(f as i64)
            } else {
                Err(format!(
                    "TypeError: int() argument must be a string, a bytes-like object or a real number, not '{kind}'",
                    kind = py_type_name(&v)
                ))
            }
        }
        Value::String(s) => s
            .trim()
            .parse::<i64>()
            .map_err(|_| format!("ValueError: invalid literal for int() with base 10: '{s}'")),
        _ => Err(format!(
            "TypeError: int() argument must be a string, a bytes-like object or a real number, not '{kind}'",
            kind = py_type_name(&v)
        )),
    }
}

fn to_float(v: &Value) -> Result<f64, String> {
    let v = unwrap_marker(v);
    match &v {
        Value::Bool(b) => Ok(if *b { 1.0 } else { 0.0 }),
        Value::Number(n) => n
            .as_f64()
            .ok_or_else(|| "TypeError: must be a real number, not str".to_string()),
        Value::String(s) => s
            .trim()
            .parse::<f64>()
            .map_err(|_| format!("ValueError: could not convert string to float: '{s}'")),
        _ => Err(format!(
            "TypeError: float() argument must be a string or a real number, not '{kind}'",
            kind = py_type_name(&v)
        )),
    }
}

fn to_bool(v: &Value) -> bool {
    let v = unwrap_marker(v);
    match &v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}

fn to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        other => {
            let other = unwrap_marker(other);
            match &other {
                Value::Number(n) => n.to_string(),
                _ => "None".to_string(),
            }
        }
    }
}

fn path_arg(v: &Value, attr: &str) -> Result<PathBuf, String> {
    if let Value::Object(m) = v
        && m.len() == 1
        && let Some(Value::String(s)) = m.get("$path")
    {
        return Ok(PathBuf::from(s));
    }
    Err(format!(
        "AttributeError: '{}' object has no attribute '{attr}'",
        py_type_name(v)
    ))
}

fn io_error(e: &std::io::Error, path: &Path) -> String {
    let known = match e.raw_os_error() {
        Some(2) => Some(("FileNotFoundError", "No such file or directory")),
        Some(13) => Some(("PermissionError", "Permission denied")),
        Some(17) => Some(("FileExistsError", "File exists")),
        Some(20) => Some(("NotADirectoryError", "Not a directory")),
        Some(21) => Some(("IsADirectoryError", "Is a directory")),
        _ => None,
    };
    match known {
        Some((class, text)) => {
            let errno = e.raw_os_error().unwrap_or(0);
            format!("{class}: [Errno {errno}] {text}: '{}'", path.display())
        }
        None => format!("OSError: {e}"),
    }
}

/// `Path.with_suffix(extra)` on the trailing suffix: "x.fasta" + extra → "x" + ".fasta" + extra.
fn with_suffix(path: &Path, extra: &str) -> PathBuf {
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let dot = name.rfind('.').filter(|i| *i > 0);
    let stem = match dot {
        Some(i) => &name[..i],
        None => name.as_str(),
    };
    let suffix = match dot {
        Some(i) => &name[i..],
        None => "",
    };
    let parent = path.parent().unwrap_or_else(|| Path::new(""));
    parent.join(format!("{stem}{suffix}{extra}"))
}

/// Python `round()` (round-half-to-even) then `int()`.
fn py_round(x: f64) -> i64 {
    let floor = x.floor();
    let frac = x - floor;
    let fl = floor as i64;
    if frac > 0.5 {
        fl + 1
    } else if frac == 0.5 {
        if fl % 2 == 0 { fl } else { fl + 1 }
    } else {
        fl
    }
}

// ---------- IUPAC matching ----------

fn bit_of(seq_base: u8) -> u8 {
    match seq_base {
        b'A' => 1,
        b'C' => 2,
        b'G' => 4,
        b'T' => 8,
        _ => 0,
    }
}

/// `IUPAC_DNA_VALUES[primer_base]` as an allowed-seq-base mask; primer `U` counts as `T`.
fn mask_of(primer_base: u8) -> u8 {
    match primer_base {
        b'A' => 1,
        b'C' => 2,
        b'G' => 4,
        b'T' | b'U' => 8,
        b'M' => 1 | 2,
        b'R' => 1 | 4,
        b'W' => 1 | 8,
        b'S' => 2 | 4,
        b'Y' => 2 | 8,
        b'K' => 4 | 8,
        b'V' => 1 | 2 | 4,
        b'H' => 1 | 2 | 8,
        b'D' => 1 | 4 | 8,
        b'B' => 2 | 4 | 8,
        b'X' | b'N' => 1 | 2 | 4 | 8,
        _ => 0,
    }
}

/// The same table keyed by full upper-case tokens, for the non-ASCII fallback.
fn allowed_of_token(primer_token: &str) -> &'static str {
    match primer_token {
        "A" => "A",
        "C" => "C",
        "G" => "G",
        "T" | "U" => "T",
        "M" => "AC",
        "R" => "AG",
        "W" => "AT",
        "S" => "CG",
        "Y" => "CT",
        "K" => "GT",
        "V" => "ACG",
        "H" => "ACT",
        "D" => "AGT",
        "B" => "CGT",
        "X" | "N" => "GATC",
        _ => "",
    }
}

// ---------- endpoint matching ----------

#[derive(Clone)]
struct Match {
    primer: String,
    overlap_bp: i64,
    mismatches: i64,
    score: i64,
    full_len_match: bool,
    trim_bp: i64,
}

fn endpoint_rank(m: &Match) -> (i64, i64, i64, i64, i64) {
    (
        m.overlap_bp,
        m.score,
        -m.mismatches,
        i64::from(m.full_len_match),
        -(m.trim_bp - m.overlap_bp),
    )
}

struct WorkSeq {
    text: String,
    ascii: Option<Vec<u8>>,
}

impl WorkSeq {
    fn new(seq_upper: &str) -> Self {
        WorkSeq {
            text: seq_upper.to_string(),
            ascii: seq_upper.is_ascii().then(|| seq_upper.as_bytes().to_vec()),
        }
    }

    fn len(&self) -> usize {
        self.ascii
            .as_ref()
            .map_or_else(|| self.text.chars().count(), Vec::len)
    }

    /// The character at `i` as its own token (Python `.upper()` of a single char).
    fn token_at(&self, i: usize) -> String {
        if self.ascii.is_some() {
            String::from(self.text.as_bytes()[i] as char)
        } else {
            self.text.chars().nth(i).unwrap_or('\0').to_string()
        }
    }

    /// `count_mismatches(work_seq[offset:offset+overlap], work_primer[-overlap:])`
    /// with the source's discard-after-count pruning folded in (a candidate over
    /// `max_mismatch` is thrown away whole, so an early exit cannot change results).
    fn count_mismatches(
        &self,
        primer: &[u8],
        ptokens: &[String],
        offset: usize,
        overlap: usize,
        max_mismatch: i64,
    ) -> i64 {
        let mut mm = 0i64;
        let bump = |mm: &mut i64| {
            *mm += 1;
            *mm > max_mismatch
        };
        if ptokens.is_empty() {
            if let Some(bytes) = &self.ascii {
                let pstart = primer.len() - overlap;
                for i in 0..overlap {
                    if mask_of(primer[pstart + i]) & bit_of(bytes[offset + i]) == 0 && bump(&mut mm)
                    {
                        return mm;
                    }
                }
            } else {
                let pstart = primer.len() - overlap;
                for i in 0..overlap {
                    let mut pbuf = [0u8; 4];
                    let p_token = char::from(primer[pstart + i])
                        .encode_utf8(&mut pbuf)
                        .to_uppercase();
                    if !matches_token(&self.token_at(offset + i), &p_token) && bump(&mut mm) {
                        return mm;
                    }
                }
            }
        } else {
            let pstart = ptokens.len() - overlap;
            for i in 0..overlap {
                if !matches_token(&self.token_at(offset + i), &ptokens[pstart + i]) && bump(&mut mm)
                {
                    return mm;
                }
            }
        }
        mm
    }

    fn reversed_view(&self) -> WorkSeq {
        let text: String = self.text.chars().rev().collect();
        WorkSeq {
            ascii: self
                .ascii
                .as_ref()
                .map(|b| b.iter().rev().copied().collect()),
            text,
        }
    }
}

/// `seq_base.upper() in IUPAC_DNA_VALUES[primer_base.upper()]` for single-character tokens.
fn matches_token(seq_token: &str, primer_token: &str) -> bool {
    seq_token.chars().count() == 1 && allowed_of_token(primer_token).contains(seq_token)
}

fn required_overlap_bp(
    primer_len: i64,
    min_overlap_bp: Option<i64>,
    min_overlap_ratio: f64,
) -> i64 {
    let ratio_required = py_round(primer_len as f64 * min_overlap_ratio);
    let ratio_required = if primer_len <= 0 {
        1
    } else {
        ratio_required.clamp(1, primer_len)
    };
    match min_overlap_bp {
        None => ratio_required,
        Some(_) if primer_len <= 0 => 1,
        Some(bp) => bp.max(ratio_required).clamp(1, primer_len),
    }
}

/// Port of `_best_endpoint_match` over one primer list for one end.
#[allow(clippy::too_many_arguments)]
fn best_endpoint(
    work_seq: &WorkSeq,
    primers: &[Primer],
    max_mismatch: i64,
    max_error_rate: f64,
    min_overlap_bp: Option<i64>,
    min_overlap_ratio: f64,
    max_offset: i64,
    from_end: bool,
) -> Option<Match> {
    let seq_len = work_seq.len() as i64;
    let mut best: Option<Match> = None;
    for primer in primers {
        // `len(primer)` in the source counts the raw string, not the upper-cased expansion.
        let primer_len = primer.original.chars().count() as i64;
        let max_overlap = primer_len.min(seq_len);
        let required = required_overlap_bp(primer_len, min_overlap_bp, min_overlap_ratio);
        if max_overlap < required {
            continue;
        }
        let max_shift = max_offset.max(0).min((seq_len - required).max(0));
        // work_primer = primer[::-1] (when from_end), upper-cased per character by the source.
        let (pbytes, ptokens): (Vec<u8>, Vec<String>) = if primer.upper.is_ascii() {
            let mut bytes = primer.upper.as_bytes().to_vec();
            if from_end {
                bytes.reverse();
            }
            (bytes, Vec::new())
        } else {
            let chars: Box<dyn Iterator<Item = char>> = if from_end {
                Box::new(primer.original.chars().rev())
            } else {
                Box::new(primer.original.chars())
            };
            (
                Vec::new(),
                chars
                    .flat_map(|c| c.to_uppercase())
                    .map(String::from)
                    .collect(),
            )
        };
        for offset in 0..=max_shift {
            let available = seq_len - offset;
            if available < required {
                continue;
            }
            let top = max_overlap.min(available);
            let mut overlap = top;
            while overlap >= required {
                let mismatches = work_seq.count_mismatches(
                    &pbytes,
                    &ptokens,
                    offset as usize,
                    overlap as usize,
                    max_mismatch,
                );
                if mismatches <= max_mismatch {
                    let error_rate = mismatches as f64 / overlap as f64;
                    if error_rate <= max_error_rate {
                        let candidate = Match {
                            primer: primer.original.clone(),
                            overlap_bp: overlap,
                            mismatches,
                            score: overlap - mismatches,
                            full_len_match: overlap == primer_len,
                            trim_bp: offset + overlap,
                        };
                        if best
                            .as_ref()
                            .is_none_or(|b| endpoint_rank(&candidate) > endpoint_rank(b))
                        {
                            best = Some(candidate);
                        }
                    }
                }
                overlap -= 1;
            }
        }
    }
    best
}

// ---------- pipeline ----------

impl Primer {
    fn new(original: &str) -> Self {
        Primer {
            original: original.to_string(),
            upper: original.to_uppercase(),
        }
    }
}

/// Bio.Seq translate-based complement: unknown characters pass through.
fn complement_char(c: char) -> char {
    match c {
        'A' => 'T',
        'C' => 'G',
        'G' => 'C',
        'T' => 'A',
        'U' => 'A',
        'a' => 't',
        'c' => 'g',
        'g' => 'c',
        't' => 'a',
        'u' => 'a',
        'R' => 'Y',
        'Y' => 'R',
        'S' => 'S',
        'W' => 'W',
        'K' => 'M',
        'M' => 'K',
        'B' => 'V',
        'V' => 'B',
        'D' => 'H',
        'H' => 'D',
        'N' => 'N',
        'X' => 'X',
        'r' => 'y',
        'y' => 'r',
        's' => 's',
        'w' => 'w',
        'k' => 'm',
        'm' => 'k',
        'b' => 'v',
        'v' => 'b',
        'd' => 'h',
        'h' => 'd',
        'n' => 'n',
        'x' => 'x',
        '-' => '-',
        other => other,
    }
}

fn reverse_complement(s: &str) -> String {
    s.chars().rev().map(complement_char).collect()
}

/// Python truthiness of a JSON value (what `options or {}` tests).
fn is_falsy(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => {
            n.as_i64().is_some_and(|x| x == 0)
                || n.as_u64().is_some_and(|x| x == 0)
                || n.as_f64().is_some_and(|x| x == 0.0)
        }
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(m) => m.is_empty(),
    }
}

/// `Seq(s)` stores its data as ASCII bytes: non-ASCII raises UnicodeEncodeError on the
/// first run of non-ASCII characters, like CPython's ascii codec.
fn seq_ascii_check(s: &str) -> Result<(), String> {
    let chars: Vec<char> = s.chars().collect();
    let Some(first) = chars.iter().position(|c| !c.is_ascii()) else {
        return Ok(());
    };
    let mut end = first;
    while end < chars.len() && !chars[end].is_ascii() {
        end += 1;
    }
    let run: String = chars[first..end].iter().collect();
    Err(format!(
        "UnicodeEncodeError: \u{1}ascii\u{1}{first}\u{1}{end}\u{1}ordinal not in range(128)\u{1}{run}"
    ))
}

fn build_ctx(
    fasta_path: &Value,
    forward_primers: &[String],
    reverse_primers: &[String],
    options: Option<&Value>,
) -> Result<Ctx, String> {
    let opts = match options {
        None => Value::Object(Row::new()),
        Some(v) if is_falsy(v) => Value::Object(Row::new()),
        Some(v) if v.is_object() => v.clone(),
        Some(other) => {
            return Err(format!(
                "AttributeError: '{}' object has no attribute 'get'",
                py_type_name(other)
            ));
        }
    };

    let min_overlap_bp_raw = opts.as_object().and_then(|m| m.get("min_overlap_bp"));
    let min_overlap_bp = match min_overlap_bp_raw {
        None | Some(Value::Null) => None,
        Some(v) => Some(to_int(v)?),
    };

    for p in forward_primers.iter().chain(reverse_primers.iter()) {
        seq_ascii_check(p)?;
    }
    let forward_rc: Vec<Primer> = forward_primers
        .iter()
        .map(|p| Primer::new(&reverse_complement(p).to_uppercase()))
        .collect();
    let reverse_rc: Vec<Primer> = reverse_primers
        .iter()
        .map(|p| Primer::new(&reverse_complement(p).to_uppercase()))
        .collect();

    let trim_mode = to_str(&opts_get(&opts, "trim_mode", json!("one_or_both")))
        .trim()
        .to_lowercase();
    let max_mismatch = to_int(&opts_get(&opts, "max_mismatch", json!(0)))?;
    let max_error_rate = to_float(&opts_get(&opts, "max_error_rate", json!(0.0)))?;
    let min_overlap_ratio = to_float(&opts_get(&opts, "min_overlap_ratio", json!(1.0)))?;
    let end_max_offset = to_int(&opts_get(&opts, "end_max_offset", json!(0)))?;
    let keep_retained_fasta = to_bool(&opts_get(&opts, "keep_retained_fasta", json!(true)));
    let iter_enable = to_bool(&opts_get(&opts, "iter_enable", json!(false)));
    let iter_max_rounds = to_int(&opts_get(&opts, "iter_max_rounds", json!(3)))?;
    let iter_stop_delta = to_float(&opts_get(&opts, "iter_stop_delta", json!(0.002)))?;
    let iter_target_conf = to_float(&opts_get(&opts, "iter_target_conf", json!(0.98)))?;
    let sidecar_format = to_str(&opts_get(&opts, "sidecar_format", json!("tsv")))
        .trim()
        .to_lowercase();
    let recheck_tool = to_str(&opts_get(&opts, "recheck_tool", json!("off")))
        .trim()
        .to_lowercase();
    let recheck_min_identity = to_float(&opts_get(&opts, "recheck_min_identity", json!(0.85)))?;
    let recheck_min_query_cov = to_float(&opts_get(&opts, "recheck_min_query_cov", json!(0.7)))?;
    let fasta = path_arg(fasta_path, "with_suffix")?;
    let retained_path = with_suffix(&fasta, ".postprep.primer.retained.fasta");

    Ok(Ctx {
        fasta,
        forward: forward_primers.iter().map(|p| Primer::new(p)).collect(),
        reverse: reverse_primers.iter().map(|p| Primer::new(p)).collect(),
        forward_rc,
        reverse_rc,
        trim_mode,
        max_mismatch,
        max_error_rate,
        min_overlap_bp,
        min_overlap_ratio,
        end_max_offset,
        keep_retained_fasta,
        iter_enable,
        iter_max_rounds,
        iter_stop_delta,
        iter_target_conf,
        sidecar_format,
        recheck_tool,
        recheck_min_identity,
        recheck_min_query_cov,
        retained_path,
    })
}

fn read_utf8(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| io_error(&e, path))?;
    String::from_utf8(bytes).map_err(|e| {
        let pos = e.utf8_error().valid_up_to();
        let byte = e.as_bytes()[pos];
        format!(
            "UnicodeDecodeError: \u{1}utf-8\u{1}{pos}\u{1}{}\u{1}invalid start byte\u{1}{byte:02x}",
            pos + 1
        )
    })
}

/// Port of `_read_primer_records`: SeqIO "fasta" parsing, then `.upper().replace("U","T")`.
fn read_primer_records(path: &Path) -> Result<Vec<(String, String)>, String> {
    let content = read_utf8(path)?;
    let normalized = content.replace("\r\n", "\n").replace('\r', "\n");
    let mut lines = normalized.lines().peekable();
    match lines.peek() {
        None => return Ok(Vec::new()),
        Some(first) if first.starts_with('>') => {}
        Some(_) => {
            return Err(
                "ValueError: This FASTA file contains comments at the beginning of the file, which are not\nallowed by the 'fasta' parser.\n\nTo parse this file, you have three options:\n\n(1) Modify your FASTA file to remove such comments at the beginning of the\nfile.\n\n(2) Use SeqIO.parse with the 'fasta-pearson' format instead of 'fasta'. This\nformat is consistent with the FASTA format defined by William Pearson's FASTA\naligner software. This format allows for comments before the first sequence;\nlines starting with the ';' character anywhere in the file are also regarded\nas comment lines and are ignored.\n\n(3) Use the 'fasta-blast' format. This parser regards any lines \"starting with\n'!', '#', or ';' as comment lines."
                    .to_string(),
            );
        }
    }

    let mut records: Vec<(String, String)> = Vec::new();
    let mut title: Option<String> = None;
    let mut seq_parts: Vec<String> = Vec::new();
    for line in lines {
        if let Some(rest) = line.strip_prefix('>') {
            if let Some(t) = title.take() {
                records.push((t, finish_seq(&seq_parts)));
                seq_parts.clear();
            }
            title = Some(rest.trim_end().to_string());
        } else {
            seq_parts.push(line.to_string());
        }
    }
    if let Some(t) = title.take() {
        records.push((t, finish_seq(&seq_parts)));
    }
    Ok(records)
}

/// The parser strips all " \\t\\r\\n" from the joined sequence, then the caller uppercases
/// and maps U to T.
fn finish_seq(parts: &[String]) -> String {
    let joined: String = parts
        .iter()
        .flat_map(|p| p.chars())
        .filter(|c| !matches!(c, ' ' | '\t' | '\r' | '\n'))
        .collect();
    joined.to_uppercase().replace('U', "T")
}

#[allow(clippy::too_many_arguments)]
fn trim_record_for_round(
    ctx: &Ctx,
    header: &str,
    seq_upper: &str,
    round_idx: i64,
    max_mismatch: i64,
    overlap_ratio: f64,
    overlap_bp: Option<i64>,
) -> (Row, Option<String>, Counts) {
    let seq_ascii = seq_upper.is_ascii();
    let work = WorkSeq::new(seq_upper);
    let work_rev = work.reversed_view();

    let canonical_left = best_endpoint(
        &work,
        &ctx.forward,
        max_mismatch,
        ctx.max_error_rate,
        overlap_bp,
        overlap_ratio,
        ctx.end_max_offset,
        false,
    );
    let canonical_right = best_endpoint(
        &work_rev,
        &ctx.reverse_rc,
        max_mismatch,
        ctx.max_error_rate,
        overlap_bp,
        overlap_ratio,
        ctx.end_max_offset,
        true,
    );
    let reverse_left = best_endpoint(
        &work,
        &ctx.reverse,
        max_mismatch,
        ctx.max_error_rate,
        overlap_bp,
        overlap_ratio,
        ctx.end_max_offset,
        false,
    );
    let reverse_right = best_endpoint(
        &work_rev,
        &ctx.forward_rc,
        max_mismatch,
        ctx.max_error_rate,
        overlap_bp,
        overlap_ratio,
        ctx.end_max_offset,
        true,
    );

    let canon = (
        matched_ends(&canonical_left, &canonical_right),
        score_total(&canonical_left, &canonical_right),
        -mismatch_total(&canonical_left, &canonical_right),
    );
    let rev = (
        matched_ends(&reverse_left, &reverse_right),
        score_total(&reverse_left, &reverse_right),
        -mismatch_total(&reverse_left, &reverse_right),
    );
    let (chosen_name, chosen_left, chosen_right, ambiguous) = if canon > rev {
        ("canonical", &canonical_left, &canonical_right, false)
    } else if rev > canon {
        ("reverse", &reverse_left, &reverse_right, false)
    } else {
        ("canonical", &canonical_left, &canonical_right, true)
    };

    let ends = matched_ends(chosen_left, chosen_right);
    let mismatches = mismatch_total(chosen_left, chosen_right);
    let confidence = confidence_label(ends, mismatches, ambiguous);

    let (do_left, do_right) = match ctx.trim_mode.as_str() {
        "one_or_both" => (chosen_left.is_some(), chosen_right.is_some()),
        "both_required" => (
            chosen_left.is_some() && chosen_right.is_some(),
            chosen_left.is_some() && chosen_right.is_some(),
        ),
        _ => (false, false),
    };
    let left_len = if do_left {
        chosen_left.as_ref().map_or(0, |m| m.trim_bp)
    } else {
        0
    };
    let right_len = if do_right {
        chosen_right.as_ref().map_or(0, |m| m.trim_bp)
    } else {
        0
    };
    let seq_len = seq_upper.chars().count() as i64;
    let right_index = if right_len > 0 {
        seq_len - right_len
    } else {
        seq_len
    };

    let mut counts = Counts::new();
    let ends_trimmed = i64::from(left_len > 0) + i64::from(right_len > 0);
    match ends_trimmed {
        2 => counts.trimmed_both += 1,
        1 if left_len > 0 => counts.trimmed_left_only += 1,
        1 => counts.trimmed_right_only += 1,
        _ => counts.untrimmed += 1,
    }
    if chosen_name == "canonical" && ends > 0 {
        counts.canonical_orientation += 1;
    }
    if chosen_name == "reverse" && ends > 0 {
        counts.reverse_orientation += 1;
    }
    match confidence {
        "high" => counts.confidence_high += 1,
        "medium" => counts.confidence_medium += 1,
        _ => counts.confidence_low += 1,
    }

    let trimmed: Option<String> = if seq_ascii {
        let bytes = seq_upper.as_bytes();
        let cut = &bytes[left_len as usize..right_index as usize];
        if cut.is_empty() {
            None
        } else {
            Some(String::from_utf8(cut.to_vec()).unwrap_or_default())
        }
    } else {
        let chars: Vec<char> = seq_upper.chars().collect();
        let cut: String = chars[left_len as usize..right_index as usize]
            .iter()
            .collect();
        if cut.is_empty() { None } else { Some(cut) }
    };
    let dropped = usize::from(trimmed.is_none()) as i64;
    counts.dropped_empty += dropped;

    let row = build_row(
        ctx,
        header,
        round_idx,
        chosen_name,
        ambiguous,
        chosen_left.as_ref(),
        chosen_right.as_ref(),
        left_len,
        right_len,
        right_index,
        confidence,
        dropped,
    );
    (row, trimmed, counts)
}

fn confidence_label(matched_ends: i64, mismatch_total: i64, ambiguous: bool) -> &'static str {
    if matched_ends == 2 && mismatch_total <= 1 && !ambiguous {
        "high"
    } else if matched_ends >= 1 {
        "medium"
    } else {
        "low"
    }
}

fn matched_ends(left: &Option<Match>, right: &Option<Match>) -> i64 {
    i64::from(left.is_some()) + i64::from(right.is_some())
}

fn score_total(left: &Option<Match>, right: &Option<Match>) -> i64 {
    left.as_ref().map_or(0, |m| m.score) + right.as_ref().map_or(0, |m| m.score)
}

fn mismatch_total(left: &Option<Match>, right: &Option<Match>) -> i64 {
    left.as_ref().map_or(0, |m| m.mismatches) + right.as_ref().map_or(0, |m| m.mismatches)
}

#[allow(clippy::too_many_arguments)]
fn build_row(
    ctx: &Ctx,
    header: &str,
    round_idx: i64,
    chosen_name: &str,
    ambiguous: bool,
    left: Option<&Match>,
    right: Option<&Match>,
    left_len: i64,
    right_len: i64,
    right_index: i64,
    confidence: &str,
    dropped_empty: i64,
) -> Row {
    let mut row = Row::new();
    row.insert("record_id".into(), json!(header));
    row.insert("round".into(), json!(round_idx));
    row.insert("orientation_chosen".into(), json!(chosen_name));
    row.insert("orientation_ambiguous".into(), json!(i64::from(ambiguous)));
    row.insert("left_hit".into(), json!(i64::from(left.is_some())));
    row.insert("right_hit".into(), json!(i64::from(right.is_some())));
    row.insert(
        "left_overlap_bp".into(),
        json!(left.map_or(0, |m| m.overlap_bp)),
    );
    row.insert(
        "right_overlap_bp".into(),
        json!(right.map_or(0, |m| m.overlap_bp)),
    );
    row.insert("left_trim_bp".into(), json!(left_len));
    row.insert("right_trim_bp".into(), json!(right_len));
    row.insert(
        "left_mismatch".into(),
        json!(left.map_or(0, |m| m.mismatches)),
    );
    row.insert(
        "right_mismatch".into(),
        json!(right.map_or(0, |m| m.mismatches)),
    );
    row.insert(
        "left_primer_name".into(),
        json!(left.map_or("", |m| m.primer.as_str())),
    );
    row.insert(
        "right_primer_name".into(),
        json!(right.map_or("", |m| m.primer.as_str())),
    );
    row.insert("trim_start".into(), json!(left_len));
    row.insert("trim_end".into(), json!(right_index));
    row.insert("trim_mode".into(), json!(ctx.trim_mode.as_str()));
    row.insert("confidence".into(), json!(confidence));
    row.insert("dropped_empty".into(), json!(dropped_empty));
    row.insert("recheck_tool".into(), json!(ctx.recheck_tool.as_str()));
    row.insert("recheck_status".into(), json!("not_run_phase2"));
    row.insert("phylo_mismatch_flag".into(), json!(0));
    row
}

fn row_i64(row: &Row, key: &str, default: i64) -> i64 {
    row.get(key)
        .map(|v| to_int(v).unwrap_or(default))
        .unwrap_or(default)
}

fn run_round(ctx: &Ctx, records: &[(String, String)], round_idx: i64) -> RoundResult {
    let relax = round_idx - 1;
    let overlap_bp = ctx.min_overlap_bp.map(|bp| 8.max(bp - relax));
    let max_mismatch = ctx.max_mismatch + relax;
    let overlap_ratio = (0.5f64).max(ctx.min_overlap_ratio - 0.05 * relax as f64);

    let mut trimmed_records = Vec::new();
    let mut rows = Vec::with_capacity(records.len());
    let mut counts = Counts::new();
    for (header, seq) in records {
        let (row, trimmed, row_counts) = trim_record_for_round(
            ctx,
            header,
            seq,
            round_idx,
            max_mismatch,
            overlap_ratio,
            overlap_bp,
        );
        counts.add(&row_counts);
        rows.push(row);
        if let Some(t) = trimmed {
            trimmed_records.push((header.clone(), t));
        }
    }
    let before = records.len() as i64;
    RoundResult {
        round: round_idx,
        records: trimmed_records,
        rows,
        counts,
        high_conf_rate: if before > 0 {
            counts.confidence_high as f64 / before as f64
        } else {
            0.0
        },
    }
}

impl Counts {
    fn add(&mut self, other: &Counts) {
        self.trimmed_both += other.trimmed_both;
        self.trimmed_left_only += other.trimmed_left_only;
        self.trimmed_right_only += other.trimmed_right_only;
        self.untrimmed += other.untrimmed;
        self.dropped_empty += other.dropped_empty;
        self.canonical_orientation += other.canonical_orientation;
        self.reverse_orientation += other.reverse_orientation;
        self.confidence_high += other.confidence_high;
        self.confidence_medium += other.confidence_medium;
        self.confidence_low += other.confidence_low;
    }
}

fn run_rounds(ctx: &Ctx, records: &[(String, String)]) -> Vec<RoundResult> {
    let round_limit = if ctx.iter_enable {
        1.max(ctx.iter_max_rounds)
    } else {
        1
    };
    let mut results = Vec::new();
    let mut previous_rate: Option<f64> = None;
    let mut round_idx = 1i64;
    while round_idx <= round_limit {
        let result = run_round(ctx, records, round_idx);
        let high_rate = result.high_conf_rate;
        results.push(result);
        if !ctx.iter_enable {
            break;
        }
        if high_rate >= ctx.iter_target_conf {
            break;
        }
        if let Some(prev) = previous_rate
            && high_rate - prev < ctx.iter_stop_delta
        {
            break;
        }
        previous_rate = Some(high_rate);
        round_idx += 1;
    }
    results
}

// ---------- recheck inputs ----------

fn aligned_sequences(records: &[(String, String)], rows: &[Row]) -> Result<Vec<String>, String> {
    if rows.len() != records.len() {
        return Err(format!(
            "ValueError: primer sidecar rows ({}) != input records ({})",
            rows.len(),
            records.len()
        ));
    }
    let mut seqs = Vec::with_capacity(records.len());
    for (index, (header, seq)) in records.iter().enumerate() {
        let row = &rows[index];
        let row_header = row.get("record_id").and_then(Value::as_str).unwrap_or("");
        if row_header != header {
            return Err(format!(
                "ValueError: primer sidecar row {index} record_id {row_header:?} != input header {header:?}"
            ));
        }
        let derived = row_i64(row, "trim_end", 0) + row_i64(row, "right_trim_bp", 0);
        let len = seq.chars().count() as i64;
        if derived != len {
            return Err(format!(
                "ValueError: primer sidecar row {index} ({header:?}) trim_end+right_trim_bp {derived} != sequence length {len}"
            ));
        }
        seqs.push(seq.clone());
    }
    Ok(seqs)
}

/// Port of `summarize_rows_to_records` used after a vsearch recheck.
fn summarize_rows(
    rows: &mut [Row],
    seqs: &[String],
    trim_mode: &str,
) -> Result<(Vec<(String, String)>, Row), String> {
    if rows.len() != seqs.len() {
        return Err(format!(
            "ValueError: primer sidecar rows ({}) != sequences ({})",
            rows.len(),
            seqs.len()
        ));
    }
    let mut counts = Counts::new();
    let mut trimmed_records: Vec<(String, String)> = Vec::new();
    for (row, seq) in rows.iter_mut().zip(seqs.iter()) {
        if seq.is_empty() {
            continue;
        }
        let confidence = row
            .get("confidence")
            .and_then(Value::as_str)
            .unwrap_or("low")
            .to_string();
        counts.confidence_high += i64::from(confidence == "high");
        counts.confidence_medium += i64::from(confidence == "medium");
        counts.confidence_low += i64::from(confidence != "high" && confidence != "medium");
        let ends = row_i64(row, "left_hit", 0) + row_i64(row, "right_hit", 0);
        if ends > 0 {
            let orientation = row
                .get("orientation_chosen")
                .and_then(Value::as_str)
                .unwrap_or("");
            if orientation == "canonical" {
                counts.canonical_orientation += 1;
            } else {
                counts.reverse_orientation += 1;
            }
        }
        let (left_len, right_len) = compute_trim_lengths(row, trim_mode);
        let ends_trimmed = i64::from(left_len > 0) + i64::from(right_len > 0);
        match ends_trimmed {
            0 => counts.untrimmed += 1,
            2 => counts.trimmed_both += 1,
            _ if left_len > 0 => counts.trimmed_left_only += 1,
            _ => counts.trimmed_right_only += 1,
        }
        let seq_len = seq.chars().count() as i64;
        let right_index = if right_len > 0 {
            seq_len - right_len
        } else {
            seq_len
        };
        row.insert("trim_start".into(), json!(left_len));
        row.insert("trim_end".into(), json!(right_index));
        let trimmed: String = if seq.is_ascii() {
            seq.as_bytes()[left_len as usize..right_index as usize]
                .iter()
                .map(|b| *b as char)
                .collect()
        } else {
            seq.chars()
                .skip(left_len as usize)
                .take((right_index - left_len) as usize)
                .collect()
        };
        if trimmed.is_empty() {
            counts.dropped_empty += 1;
            row.insert("dropped_empty".into(), json!(1));
        } else {
            row.insert("dropped_empty".into(), json!(0));
            let record_id = row
                .get("record_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            trimmed_records.push((record_id, trimmed));
        }
    }
    let before = rows.len() as i64;
    let mut summary = Row::new();
    summary.insert("before".into(), json!(before));
    summary.insert("after".into(), json!(trimmed_records.len() as i64));
    summary.insert(
        "removed".into(),
        json!(before - trimmed_records.len() as i64),
    );
    insert_counts(&mut summary, &counts);
    summary.insert(
        "high_conf_rate".into(),
        json!(if before > 0 {
            counts.confidence_high as f64 / before as f64
        } else {
            0.0
        }),
    );
    Ok((trimmed_records, summary))
}

fn compute_trim_lengths(row: &Row, trim_mode: &str) -> (i64, i64) {
    let left_hit = row_i64(row, "left_hit", 0) > 0;
    let right_hit = row_i64(row, "right_hit", 0) > 0;
    let left_trim = row_i64(row, "left_trim_bp", 0);
    let right_trim = row_i64(row, "right_trim_bp", 0);
    let left_overlap = if left_trim > 0 {
        left_trim
    } else {
        row_i64(row, "left_overlap_bp", 0)
    };
    let right_overlap = if right_trim > 0 {
        right_trim
    } else {
        row_i64(row, "right_overlap_bp", 0)
    };
    match trim_mode {
        "one_or_both" => (
            if left_hit { left_overlap } else { 0 },
            if right_hit { right_overlap } else { 0 },
        ),
        "both_required" if left_hit && right_hit => (left_overlap, right_overlap),
        "both_required" => (0, 0),
        _ => (0, 0),
    }
}

fn insert_counts(summary: &mut Row, counts: &Counts) {
    summary.insert("trimmed_both".into(), json!(counts.trimmed_both));
    summary.insert("trimmed_left_only".into(), json!(counts.trimmed_left_only));
    summary.insert(
        "trimmed_right_only".into(),
        json!(counts.trimmed_right_only),
    );
    summary.insert("untrimmed".into(), json!(counts.untrimmed));
    summary.insert("dropped_empty".into(), json!(counts.dropped_empty));
    summary.insert(
        "canonical_orientation".into(),
        json!(counts.canonical_orientation),
    );
    summary.insert(
        "reverse_orientation".into(),
        json!(counts.reverse_orientation),
    );
    summary.insert("confidence_high".into(), json!(counts.confidence_high));
    summary.insert("confidence_medium".into(), json!(counts.confidence_medium));
    summary.insert("confidence_low".into(), json!(counts.confidence_low));
}

// ---------- outputs ----------

fn write_outputs(
    ctx: &Ctx,
    best_records: &[(String, String)],
    sidecar_rows: &[Row],
) -> Result<Option<PathBuf>, String> {
    let tmp_path = with_suffix(&ctx.fasta, ".postprep.primer.tmp");
    let write_result = (|| -> Result<(), String> {
        let mut out = fs::File::create(&tmp_path).map_err(|e| io_error(&e, &tmp_path))?;
        for (header, seq) in best_records {
            out.write_all(b">").map_err(map_io)?;
            out.write_all(header.as_bytes()).map_err(map_io)?;
            out.write_all(b"\n").map_err(map_io)?;
            out.write_all(seq.as_bytes()).map_err(map_io)?;
            out.write_all(b"\n").map_err(map_io)?;
        }
        out.flush().map_err(|e| io_error(&e, &tmp_path))?;
        fs::rename(&tmp_path, &ctx.fasta).map_err(|e| io_error(&e, &ctx.fasta))
    })();
    if tmp_path.exists() {
        let _ = fs::remove_file(&tmp_path);
    }
    write_result?;

    if ctx.sidecar_format == "jsonl" {
        let sidecar_path = with_suffix(&ctx.fasta, ".postprep.primer.jsonl");
        let mut f = fs::File::create(&sidecar_path).map_err(|e| io_error(&e, &sidecar_path))?;
        for row in sidecar_rows {
            let line = json_dumps(row);
            f.write_all(line.as_bytes()).map_err(map_io)?;
            f.write_all(b"\n").map_err(map_io)?;
        }
        f.flush().map_err(|e| io_error(&e, &sidecar_path))?;
        Ok(Some(sidecar_path))
    } else {
        let sidecar_path = with_suffix(&ctx.fasta, ".postprep.primer.tsv");
        let mut f = fs::File::create(&sidecar_path).map_err(|e| io_error(&e, &sidecar_path))?;
        let fields = [
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
        ];
        let mut buf = String::new();
        for name in fields {
            tsv_field(&mut buf, name);
            buf.push('\t');
        }
        buf.pop();
        buf.push_str("\r\n");
        for row in sidecar_rows {
            for name in fields {
                let value = match row.get(name) {
                    None | Some(Value::Null) => String::new(),
                    Some(Value::String(s)) => s.clone(),
                    Some(other) => value_str(other),
                };
                tsv_field(&mut buf, &value);
                buf.push('\t');
            }
            buf.pop();
            buf.push_str("\r\n");
        }
        f.write_all(buf.as_bytes()).map_err(map_io)?;
        f.flush().map_err(|e| io_error(&e, &sidecar_path))?;
        Ok(Some(sidecar_path))
    }
}

fn map_io(e: std::io::Error) -> String {
    format!("OSError: {e}")
}

fn value_str(v: &Value) -> String {
    match v {
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::Number(n) => n.to_string(),
        _ => v.to_string(),
    }
}

fn tsv_field(buf: &mut String, value: &str) {
    if value.chars().any(|c| matches!(c, '\t' | '"' | '\r' | '\n')) {
        buf.push('"');
        for c in value.chars() {
            if c == '"' {
                buf.push('"');
            }
            buf.push(c);
        }
        buf.push('"');
    } else {
        buf.push_str(value);
    }
}

/// `json.dumps(row, ensure_ascii=False)` for a flat str/int object.
fn json_dumps(row: &Row) -> String {
    let mut out = String::from("{");
    for (i, (key, value)) in row.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        json_escape_into(&mut out, key);
        out.push_str(": ");
        match value {
            Value::String(s) => json_escape_into(&mut out, s),
            Value::Number(n) => out.push_str(&n.to_string()),
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Value::Null => out.push_str("null"),
            other => out.push_str(&other.to_string()),
        }
    }
    out.push('}');
    out
}

fn json_escape_into(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

// ---------- entry point ----------

/// Port of `apply_post_prep_primer_trim` (`taxondbbuilder/postprep/primer_trim.py:1117`).
///
/// `options` defaults to `None` in the source.
pub fn apply_post_prep_primer_trim(
    fasta_path: serde_json::Value,
    forward_primers: Vec<String>,
    reverse_primers: Vec<String>,
    options: Option<serde_json::Value>,
    recheck: &RecheckFn<'_>,
) -> Result<serde_json::Value, String> {
    let ctx = build_ctx(
        &fasta_path,
        &forward_primers,
        &reverse_primers,
        options.as_ref(),
    )?;

    if ctx.keep_retained_fasta {
        fs::copy(&ctx.fasta, &ctx.retained_path).map_err(|e| io_error(&e, &ctx.retained_path))?;
    }
    let records = read_primer_records(&ctx.fasta)?;

    if records.is_empty() {
        let mut out = Row::new();
        out.insert("before".into(), json!(0));
        out.insert("after".into(), json!(0));
        out.insert("removed".into(), json!(0));
        insert_counts(&mut out, &Counts::new());
        out.insert("rounds_run".into(), json!(0));
        out.insert("best_round".into(), json!(0));
        out.insert("high_conf_rate".into(), json!(0.0));
        out.insert("sidecar_path".into(), Value::Null);
        out.insert(
            "retained_path".into(),
            if ctx.keep_retained_fasta {
                json!(ctx.retained_path.display().to_string())
            } else {
                Value::Null
            },
        );
        out.insert("recheck_tool".into(), json!(ctx.recheck_tool.as_str()));
        return Ok(Value::Object(out));
    }

    let results = run_rounds(&ctx, &records);
    let mut best_idx = 0usize;
    for (i, r) in results.iter().enumerate().skip(1) {
        let cur = &results[best_idx];
        let key = |r: &RoundResult| {
            (
                r.high_conf_rate,
                r.records.len() as i64,
                -r.counts.dropped_empty,
                r.round,
            )
        };
        if key(r) > key(cur) {
            best_idx = i;
        }
    }
    let best = &results[best_idx];
    let best_round = best.round;
    let mut best_records = best.records.clone();
    let mut counts = best.counts;
    let mut high_conf_rate = best.high_conf_rate;
    let mut rows: Vec<Row> = results
        .iter()
        .flat_map(|r| r.rows.iter())
        .filter(|row| row_i64(row, "round", 0) == best_round)
        .cloned()
        .collect();

    let (recheck_attempted, recheck_rescued, recheck_error) = match ctx.recheck_tool.as_str() {
        "off" => (0, 0, None),
        "vsearch" => {
            let seqs = aligned_sequences(&records, &rows)?;
            let (attempted, rescued, error) = recheck(&ctx, &mut rows, &seqs)?;
            let (rebuilt_records, rebuilt_summary) =
                summarize_rows(&mut rows, &seqs, &ctx.trim_mode)?;
            best_records = rebuilt_records;
            counts = Counts {
                trimmed_both: row_i64(&rebuilt_summary, "trimmed_both", 0),
                trimmed_left_only: row_i64(&rebuilt_summary, "trimmed_left_only", 0),
                trimmed_right_only: row_i64(&rebuilt_summary, "trimmed_right_only", 0),
                untrimmed: row_i64(&rebuilt_summary, "untrimmed", 0),
                dropped_empty: row_i64(&rebuilt_summary, "dropped_empty", 0),
                canonical_orientation: row_i64(&rebuilt_summary, "canonical_orientation", 0),
                reverse_orientation: row_i64(&rebuilt_summary, "reverse_orientation", 0),
                confidence_high: row_i64(&rebuilt_summary, "confidence_high", 0),
                confidence_medium: row_i64(&rebuilt_summary, "confidence_medium", 0),
                confidence_low: row_i64(&rebuilt_summary, "confidence_low", 0),
            };
            high_conf_rate = rebuilt_summary
                .get("high_conf_rate")
                .and_then(Value::as_f64)
                .unwrap_or(high_conf_rate);
            (attempted, rescued, error)
        }
        other => (0, 0, Some(format!("unsupported_tool:{other}"))),
    };

    let sidecar_path = write_outputs(&ctx, &best_records, &rows)?;

    let before = records.len() as i64;
    let after = best_records.len() as i64;
    let mut out = Row::new();
    out.insert("before".into(), json!(before));
    out.insert("after".into(), json!(after));
    out.insert("removed".into(), json!(before - after));
    insert_counts(&mut out, &counts);
    out.insert("high_conf_rate".into(), json!(high_conf_rate));
    out.insert("rounds_run".into(), json!(results.len() as i64));
    out.insert("best_round".into(), json!(best_round));
    out.insert(
        "sidecar_path".into(),
        sidecar_path
            .map(|p| json!(p.display().to_string()))
            .unwrap_or(Value::Null),
    );
    out.insert(
        "retained_path".into(),
        if ctx.keep_retained_fasta {
            json!(ctx.retained_path.display().to_string())
        } else {
            Value::Null
        },
    );
    out.insert("recheck_tool".into(), json!(ctx.recheck_tool.as_str()));
    out.insert("recheck_attempted".into(), json!(recheck_attempted));
    out.insert("recheck_rescued".into(), json!(recheck_rescued));
    out.insert(
        "recheck_error".into(),
        recheck_error.map(|e| json!(e)).unwrap_or(Value::Null),
    );
    Ok(Value::Object(out))
}

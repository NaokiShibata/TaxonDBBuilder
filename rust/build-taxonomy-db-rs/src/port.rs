//! The Rust port of `tools/build_taxonomy_db.py`.
//!
//! Return `Err("Kind: text")` where the source throws, so the Python caller gets the same
//! exception type. Path arguments arrive from `pyconv::from_py` as `{"$path": "..."}` (see
//! the `__fspath__` branch there); anything else reproduces the source's AttributeError.

#![forbid(unsafe_code)]

use serde_json::Value;
use std::fs;
use std::io::{BufRead, Read, Seek};
use std::path::{Path, PathBuf};

const CREATE_TABLE_SQL: &str = "
            CREATE TABLE taxonomy (
                tax_id INTEGER PRIMARY KEY,
                scientific_name TEXT NOT NULL
            )
            ";

const INDEX_SQL: &str =
    "CREATE INDEX idx_taxonomy_scientific_name_nocase ON taxonomy(scientific_name COLLATE NOCASE)";

/// Python's type name for a bridged JSON value, for AttributeError messages.
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
                if matches!(m.get("$num"), Some(Value::String(_))) {
                    return "float";
                }
            }
            "dict"
        }
    }
}

/// A `{"$path": s}` value as a path; otherwise the AttributeError the source raises on
/// calling `.<attr>` of a non-Path object.
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

/// An io error as the Python exception the source would raise for it.
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

/// Read a file as UTF-8 text, as Python's `path.open("r", encoding="utf-8")` does:
/// streaming, one line at a time, so memory stays bounded and a bad byte mid-file leaves
/// everything read before it already handled, like the source's line iteration.
/// Universal newlines are applied per line, as Python's text mode does.
fn stream_lines(
    path: &Path,
    mut on_line: impl FnMut(&str) -> Result<(), String>,
) -> Result<(), String> {
    let file = fs::File::open(path).map_err(|e| io_error(&e, path))?;
    let mut reader = std::io::BufReader::new(file);
    let mut buf: Vec<u8> = Vec::new();
    let mut line_start: usize = 0;
    loop {
        buf.clear();
        let n = reader
            .read_until(b'\n', &mut buf)
            .map_err(|e| io_error(&e, path))?;
        if n == 0 {
            break;
        }
        let mut data = &buf[..];
        let had_nl = data.ends_with(b"\n");
        if data.ends_with(b"\n") {
            data = &data[..data.len() - 1];
        }
        if data.ends_with(b"\r") {
            data = &data[..data.len() - 1];
        }
        // A bare \r also ends a line in text mode; split it into segments.
        let mut off: usize = 0;
        for seg in data.split(|b| *b == b'\r') {
            let line = std::str::from_utf8(seg)
                .map_err(|e| decode_error(seg, e, line_start + off, had_nl))
                .map(str::to_owned)?;
            on_line(&line)?;
            off += seg.len() + 1;
        }
        line_start += n;
    }
    Ok(())
}

/// Python's `UnicodeDecodeError` for a failed UTF-8 validation of one line, with the
/// absolute byte position in the file, as `\u{1}`-separated parts the binding turns into
/// a real `UnicodeDecodeError` (its constructor takes five arguments).
/// `\r` line breaks never cut a sequence, so an incomplete sequence only occurs at end of input.
fn decode_error(seg: &[u8], e: std::str::Utf8Error, line_pos: usize, had_nl: bool) -> String {
    let start = line_pos + e.valid_up_to();
    match e.error_len() {
        Some(1) if (0xc0..=0xf7).contains(&seg[e.valid_up_to()]) => format!(
            "UnicodeDecodeError: \u{1}utf-8\u{1}{start}\u{1}{}\u{1}invalid continuation byte\u{1}{:02x}",
            start + 1,
            seg[e.valid_up_to()]
        ),
        Some(_) => format!(
            "UnicodeDecodeError: \u{1}utf-8\u{1}{start}\u{1}{}\u{1}invalid start byte\u{1}{:02x}",
            start + 1,
            seg[e.valid_up_to()]
        ),
        None => {
            // An incomplete sequence at end of input: the last decode call only holds
            // those bytes, so Python reports them at position 0 of that buffer; one cut
            // by a newline is an invalid continuation at the real position.
            let rest = &seg[e.valid_up_to()..];
            let hex: String = rest.iter().map(|b| format!("{b:02x}")).collect();
            let (start, end, reason) = if had_nl {
                (start, start + rest.len(), "invalid continuation byte")
            } else {
                (0, rest.len(), "unexpected end of data")
            };
            format!("UnicodeDecodeError: \u{1}utf-8\u{1}{start}\u{1}{end}\u{1}{reason}\u{1}{hex}")
        }
    }
}

/// `output.parent.mkdir(parents=True, exist_ok=True)` on the path's parent.
fn mkdir_parent(path: &Path) -> Result<(), String> {
    let mut parent: PathBuf = path.parent().map_or_else(|| PathBuf::from("."), Into::into);
    if parent.as_os_str().is_empty() {
        parent = PathBuf::from(".");
    }
    fs::create_dir_all(&parent).map_err(|e| io_error(&e, &parent))
}

/// `tax_id_s.isdigit()` then `int(tax_id_s)` then `str(...)`: the canonical decimal digits.
/// shortcut: ASCII digits only; Python also accepts Unicode decimal digits, names.dmp is ASCII.
fn tax_id_digits(s: &str) -> Option<String> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let trimmed = s.trim_start_matches('0');
    Some(if trimmed.is_empty() {
        "0".to_string()
    } else {
        trimmed.to_string()
    })
}

/// `sqlite3.executemany` converting the id to SQLite INTEGER.
fn to_i64(digits: &str) -> Result<i64, String> {
    digits
        .parse::<i64>()
        .map_err(|_| "OverflowError: Python int too large to convert to SQLite INTEGER".to_string())
}

fn sqlite_err(e: rusqlite::Error) -> String {
    if let rusqlite::Error::SqliteFailure(err, Some(msg)) = &e
        && err.code == rusqlite::ErrorCode::ConstraintViolation
    {
        return format!("sqlite3.IntegrityError: {msg}");
    }
    format!("sqlite3.OperationalError: {e}")
}

/// Port of `extract_scientific_names_csv` (`tools/build_taxonomy_db.py:45`).
pub fn extract_scientific_names_csv(
    names_dmp: serde_json::Value,
    output_csv: serde_json::Value,
) -> Result<i64, String> {
    let out = path_arg(&output_csv, "parent")?;
    mkdir_parent(&out)?;
    let file = fs::File::create(&out).map_err(|e| io_error(&e, &out))?;
    let mut writer = csv::WriterBuilder::new()
        .terminator(csv::Terminator::CRLF)
        .from_writer(file);
    writer
        .write_record(["tax_id", "scientific_name"])
        .map_err(|e| format!("OSError: {e}"))?;

    let src = path_arg(&names_dmp, "open")?;
    let mut count: i64 = 0;
    stream_lines(&src, |line| {
        if line.is_empty() {
            return Ok(());
        }
        let line = line.strip_suffix("\t|").unwrap_or(line);
        let parts: Vec<&str> = line.split("\t|\t").collect();
        if parts.len() < 4 {
            return Ok(());
        }
        let tax_id = parts[0].trim();
        let name = parts[1].trim();
        if parts[3].trim() != "scientific name" {
            return Ok(());
        }
        let Some(digits) = tax_id_digits(tax_id) else {
            return Ok(());
        };
        if name.is_empty() {
            return Ok(());
        }
        writer
            .write_record([digits.as_str(), name])
            .map_err(|e| format!("OSError: {e}"))?;
        count += 1;
        Ok(())
    })?;
    writer.flush().map_err(|e| format!("OSError: {e}"))?;
    Ok(count)
}

/// Port of `iter_csv_rows` + the header validation (`tools/build_taxonomy_db.py:57`),
/// streaming one record at a time into `on_row`.
fn stream_rows(
    src: &Path,
    mut on_row: impl FnMut(String, &str) -> Result<(), String>,
) -> Result<(), String> {
    let file = fs::File::open(src).map_err(|e| io_error(&e, src))?;
    if file.metadata().map_err(|e| io_error(&e, src))?.len() == 0 {
        return Err(format!("ValueError: CSV has no header: {}", src.display()));
    }
    // DictReader takes the first physical line as fieldnames: a BOM becomes part of the
    // first header, and a leading blank line yields the header [""]; neither matches.
    // The csv crate strips a BOM and skips leading blank lines, so reject them here.
    let mut reader = std::io::BufReader::new(file);
    let mut head = [0u8; 3];
    let got = reader.read(&mut head).map_err(|e| io_error(&e, src))?;
    let head = &head[..got];
    if head.starts_with(&[0xef, 0xbb, 0xbf]) || matches!(head.first(), Some(b'\n') | Some(b'\r')) {
        return Err("ValueError: CSV must contain headers: tax_id, scientific_name".to_string());
    }
    let mut rdr = csv::ReaderBuilder::new()
        .flexible(true)
        .from_reader(head.chain(reader));
    let headers = rdr.headers().map_err(|e| csv_error(e, src))?.clone();
    for h in headers.iter() {
        check_field_size(h)?;
    }
    // `headers = {h.strip(): h for h in fieldnames if h}` keeps the last header whose
    // stripped text matches; DictReader keys rows by the raw header, so a repeated key
    // then reads the last column carrying that exact text.
    let find = |want: &str| -> Option<usize> {
        let mut chosen = None;
        for (i, h) in headers.iter().enumerate() {
            if !h.is_empty() && h.trim() == want {
                chosen = Some(i);
            }
        }
        let text = &headers[chosen?];
        let mut last = None;
        for (i, h) in headers.iter().enumerate() {
            if h == text {
                last = Some(i);
            }
        }
        last
    };
    let (tax_idx, sci_idx) = match (find("tax_id"), find("scientific_name")) {
        (Some(tax), Some(sci)) => (tax, sci),
        _ => {
            return Err(
                "ValueError: CSV must contain headers: tax_id, scientific_name".to_string(),
            );
        }
    };

    for result in rdr.records() {
        let record = result.map_err(|e| csv_error(e, src))?;
        for field in record.iter() {
            check_field_size(field)?;
        }
        // `str(row.get(col, ""))`: a short row reads DictReader's restval None as "None".
        let cell = |idx: usize| -> String {
            match record.get(idx) {
                Some(v) => v.trim().to_string(),
                None => "None".to_string(),
            }
        };
        let tax_id = cell(tax_idx);
        let name = cell(sci_idx);
        let Some(digits) = tax_id_digits(&tax_id) else {
            continue;
        };
        if name.is_empty() {
            continue;
        }
        on_row(digits, &name)?;
    }
    Ok(())
}

/// `csv.field_size_limit()` defaults to 131072 bytes; the reader raises on any longer
/// field, checked in read order.
fn check_field_size(field: &str) -> Result<(), String> {
    if field.len() > 131072 {
        return Err("csv.Error: field larger than field limit (131072)".to_string());
    }
    Ok(())
}

/// A csv crate read error as the Python error the source raises for it.
fn csv_error(e: csv::Error, src: &Path) -> String {
    if let csv::ErrorKind::Utf8 { pos: Some(p), .. } = e.kind() {
        // Python names the offending byte; re-read it at the reported position.
        if let Ok(mut f) = fs::File::open(src)
            && let Ok(_) = f.seek(std::io::SeekFrom::Start(p.byte()))
        {
            let mut b = [0u8; 1];
            if f.read(&mut b).unwrap_or(0) == 1 {
                return format!(
                    "UnicodeDecodeError: \u{1}utf-8\u{1}{}\u{1}{}\u{1}invalid start byte\u{1}{:02x}",
                    p.byte(),
                    p.byte() + 1,
                    b[0]
                );
            }
        }
    }
    format!("OSError: {e}")
}

/// Port of `build_sqlite_from_csv` (`tools/build_taxonomy_db.py:78`).
pub fn build_sqlite_from_csv(
    input_csv: serde_json::Value,
    output_db: serde_json::Value,
) -> Result<i64, String> {
    let db = path_arg(&output_db, "parent")?;
    mkdir_parent(&db)?;
    if db.exists() {
        fs::remove_file(&db).map_err(|e| io_error(&e, &db))?;
    }
    let mut conn = rusqlite::Connection::open(&db).map_err(sqlite_err)?;
    conn.execute_batch(CREATE_TABLE_SQL).map_err(sqlite_err)?;

    let src = path_arg(&input_csv, "open")?;
    let mut inserted: i64 = 0;
    {
        let tx = conn.transaction().map_err(sqlite_err)?;
        {
            let mut stmt = tx
                .prepare("INSERT INTO taxonomy (tax_id, scientific_name) VALUES (?, ?)")
                .map_err(sqlite_err)?;
            stream_rows(&src, |digits, name| {
                let tax_id = to_i64(&digits)?;
                stmt.execute(rusqlite::params![tax_id, name])
                    .map_err(sqlite_err)?;
                inserted += 1;
                Ok(())
            })?;
        }
        tx.execute(INDEX_SQL, []).map_err(sqlite_err)?;
        tx.commit().map_err(sqlite_err)?;
    }
    Ok(inserted)
}

/// Port of `build_from_names_dmp` (`tools/build_taxonomy_db.py:113`).
pub fn build_from_names_dmp(
    names_dmp: serde_json::Value,
    output_csv: serde_json::Value,
    output_db: serde_json::Value,
) -> Result<(i64, i64), String> {
    let extracted = extract_scientific_names_csv(names_dmp, output_csv.clone())?;
    let inserted = build_sqlite_from_csv(output_csv, output_db)?;
    Ok((extracted, inserted))
}

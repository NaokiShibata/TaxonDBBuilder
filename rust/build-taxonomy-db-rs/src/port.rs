//! The Rust port of `tools/build_taxonomy_db.py`.
//!
//! Return `Err("Kind: text")` where the source throws, so the Python caller gets the same
//! exception type. Path arguments arrive from `pyconv::from_py` as `{"$path": "..."}` (see
//! the `__fspath__` branch there); anything else reproduces the source's AttributeError.

#![forbid(unsafe_code)]

use serde_json::{Value, json};
use std::fs;
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

/// Read a file as UTF-8 text, as Python's `path.open("r", encoding="utf-8")` does.
fn read_utf8(path: &PathBuf) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| io_error(&e, path))?;
    String::from_utf8(bytes).map_err(|e| {
        let pos = e.utf8_error().valid_up_to();
        let byte = e.as_bytes()[pos];
        format!(
            "UnicodeDecodeError: 'utf-8' codec can't decode byte 0x{byte:02x} in position {pos}: invalid start byte"
        )
    })
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
    let content = read_utf8(&src)?;
    // Python's universal newlines: \r\n, \r and \n all end a line.
    let lines = content.replace("\r\n", "\n").replace('\r', "\n");
    let mut count: i64 = 0;
    for line in lines.split('\n') {
        if line.is_empty() {
            continue;
        }
        let line = line.strip_suffix("\t|").unwrap_or(line);
        let parts: Vec<&str> = line.split("\t|\t").collect();
        if parts.len() < 4 {
            continue;
        }
        let tax_id = parts[0].trim();
        let name = parts[1].trim();
        if parts[3].trim() != "scientific name" {
            continue;
        }
        let Some(digits) = tax_id_digits(tax_id) else {
            continue;
        };
        if name.is_empty() {
            continue;
        }
        writer
            .write_record([digits.as_str(), name])
            .map_err(|e| format!("OSError: {e}"))?;
        count += 1;
    }
    writer.flush().map_err(|e| format!("OSError: {e}"))?;
    Ok(count)
}

/// Port of `iter_csv_rows` + the header validation (`tools/build_taxonomy_db.py:57`),
/// returning the rows the source yields.
fn parse_rows(content: &str, src: &Path) -> Result<Vec<(String, String)>, String> {
    if content.is_empty() {
        return Err(format!("ValueError: CSV has no header: {}", src.display()));
    }
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .from_reader(content.as_bytes());
    let headers = reader
        .headers()
        .map_err(|e| format!("OSError: {e}"))?
        .clone();
    // DictReader keys rows by header text; the source picks, per wanted column, the last
    // header whose stripped text matches, and reads the first column with that exact text.
    let find = |want: &str| -> Option<usize> {
        let mut chosen = None;
        for (i, h) in headers.iter().enumerate() {
            if h.trim() == want {
                chosen = Some(i);
            }
        }
        let chosen = chosen?;
        headers.iter().position(|h| h == &headers[chosen])
    };
    let (tax_idx, sci_idx) = match (find("tax_id"), find("scientific_name")) {
        (Some(tax), Some(sci)) => (tax, sci),
        _ => {
            return Err(
                "ValueError: CSV must contain headers: tax_id, scientific_name".to_string(),
            );
        }
    };

    let mut rows = Vec::new();
    for result in reader.records() {
        let record = result.map_err(|e| format!("OSError: {e}"))?;
        let tax_id = record.get(tax_idx).unwrap_or("").trim();
        let name = record.get(sci_idx).unwrap_or("").trim();
        let Some(digits) = tax_id_digits(tax_id) else {
            continue;
        };
        if name.is_empty() {
            continue;
        }
        rows.push((digits, name.to_string()));
    }
    Ok(rows)
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
    let content = read_utf8(&src)?;
    let rows = parse_rows(&content, &src)?;

    let mut inserted: i64 = 0;
    {
        let tx = conn.transaction().map_err(sqlite_err)?;
        {
            let mut stmt = tx
                .prepare("INSERT INTO taxonomy (tax_id, scientific_name) VALUES (?, ?)")
                .map_err(sqlite_err)?;
            for (digits, name) in &rows {
                let tax_id = to_i64(digits)?;
                stmt.execute(rusqlite::params![tax_id, name])
                    .map_err(sqlite_err)?;
                inserted += 1;
            }
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
) -> Result<serde_json::Value, String> {
    let extracted = extract_scientific_names_csv(names_dmp, output_csv.clone())?;
    let inserted = build_sqlite_from_csv(output_csv, output_db)?;
    Ok(json!([extracted, inserted]))
}

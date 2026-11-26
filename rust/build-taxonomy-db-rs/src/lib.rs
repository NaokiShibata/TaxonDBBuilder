//! Python bindings for `tools/build_taxonomy_db.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use pyo3::prelude::*;

pub mod js;
pub mod port;
pub mod pyconv;
pub mod pynum;

#[allow(unused_imports)]
use pynum::Num;

fn error(message: String) -> PyErr {
    use pyo3::exceptions as e;
    /// `Err("Kind: text")` for a `Kind` the match below does not know: a built-in exception's name or
    /// `package.module.Class`, called with the text. None when it names no `Exception` subclass or the
    /// class cannot be made from the text alone (its constructor takes more arguments).
    fn named_error(kind: &str, text: &str) -> Option<PyErr> {
        Python::attach(|py| {
            // Each dotted part must be a Python identifier, by Python's own rules (`str.isidentifier`).
            let identifier = |part: &str| {
                pyo3::types::PyString::new(py, part)
                    .call_method0("isidentifier")
                    .and_then(|ok| ok.extract::<bool>())
                    .unwrap_or(false)
            };
            if !kind.split('.').all(identifier) {
                return None;
            }
            let (module, name) = kind.rsplit_once('.').unwrap_or(("builtins", kind));
            let class = py.import(module).ok()?.getattr(name).ok()?;
            let class = class.cast_into::<pyo3::types::PyType>().ok()?;
            if !class
                .is_subclass_of::<pyo3::exceptions::PyException>()
                .ok()?
            {
                return None;
            }
            let value = class.call1((text,)).ok()?;
            if !value.is_instance(&class).ok()? {
                return None;
            }
            Some(PyErr::from_value(value))
        })
    }
    let (kind, text) = message.split_once(": ").unwrap_or(("", message.as_str()));
    let text = text.to_string();
    match kind {
        "TypeError" => e::PyTypeError::new_err(text),
        "KeyError" => e::PyKeyError::new_err(text),
        "IndexError" => e::PyIndexError::new_err(text),
        "OverflowError" => e::PyOverflowError::new_err(text),
        "ZeroDivisionError" => e::PyZeroDivisionError::new_err(text),
        "AttributeError" => e::PyAttributeError::new_err(text),
        "RuntimeError" => e::PyRuntimeError::new_err(text),
        "NotImplementedError" => e::PyNotImplementedError::new_err(text),
        "LookupError" => e::PyLookupError::new_err(text),
        "ArithmeticError" => e::PyArithmeticError::new_err(text),
        "UnicodeError" => e::PyUnicodeError::new_err(text),
        "ValueError" => e::PyValueError::new_err(text),
        _ => named_error(kind, &text).unwrap_or_else(|| e::PyValueError::new_err(message)),
    }
}

fn extract_scientific_names_csv_inner(
    names_dmp: Bound<'_, PyAny>,
    output_csv: Bound<'_, PyAny>,
) -> Result<i64, String> {
    let names_dmp: serde_json::Value = crate::pyconv::FromPy::from_py(&names_dmp)?;
    let output_csv: serde_json::Value = crate::pyconv::FromPy::from_py(&output_csv)?;
    let out = port::extract_scientific_names_csv(names_dmp, output_csv)?;
    Ok(out)
}

/// `extract_scientific_names_csv`.
#[pyfunction]
#[pyo3(name = "extract_scientific_names_csv", signature = (names_dmp, output_csv))]
fn extract_scientific_names_csv(
    names_dmp: Bound<'_, PyAny>,
    output_csv: Bound<'_, PyAny>,
) -> PyResult<i64> {
    extract_scientific_names_csv_inner(names_dmp, output_csv).map_err(error)
}

fn build_sqlite_from_csv_inner(
    input_csv: Bound<'_, PyAny>,
    output_db: Bound<'_, PyAny>,
) -> Result<i64, String> {
    let input_csv: serde_json::Value = crate::pyconv::FromPy::from_py(&input_csv)?;
    let output_db: serde_json::Value = crate::pyconv::FromPy::from_py(&output_db)?;
    let out = port::build_sqlite_from_csv(input_csv, output_db)?;
    Ok(out)
}

/// `build_sqlite_from_csv`.
#[pyfunction]
#[pyo3(name = "build_sqlite_from_csv", signature = (input_csv, output_db))]
fn build_sqlite_from_csv(
    input_csv: Bound<'_, PyAny>,
    output_db: Bound<'_, PyAny>,
) -> PyResult<i64> {
    build_sqlite_from_csv_inner(input_csv, output_db).map_err(error)
}

fn build_from_names_dmp_inner(
    names_dmp: Bound<'_, PyAny>,
    output_csv: Bound<'_, PyAny>,
    output_db: Bound<'_, PyAny>,
) -> Result<serde_json::Value, String> {
    let names_dmp: serde_json::Value = crate::pyconv::FromPy::from_py(&names_dmp)?;
    let output_csv: serde_json::Value = crate::pyconv::FromPy::from_py(&output_csv)?;
    let output_db: serde_json::Value = crate::pyconv::FromPy::from_py(&output_db)?;
    let out = port::build_from_names_dmp(names_dmp, output_csv, output_db)?;
    Ok(out)
}

/// `build_from_names_dmp`.
#[pyfunction]
#[pyo3(name = "build_from_names_dmp", signature = (names_dmp, output_csv, output_db))]
fn build_from_names_dmp(
    py: Python<'_>,
    names_dmp: Bound<'_, PyAny>,
    output_csv: Bound<'_, PyAny>,
    output_db: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    build_from_names_dmp_inner(names_dmp, output_csv, output_db)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_scientific_names_csv, m)?)?;
    m.add_function(wrap_pyfunction!(build_sqlite_from_csv, m)?)?;
    m.add_function(wrap_pyfunction!(build_from_names_dmp, m)?)?;
    Ok(())
}

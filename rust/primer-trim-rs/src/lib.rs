//! Python bindings for `taxondbbuilder/postprep/primer_trim.py`: generated from the signatures in `port.rs` by rust-anything.
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
    if let Some(err) = unicode_error(&message) {
        return err;
    }
    /// `UnicodeEncodeError: \x01enc\x01start\x01end\x01reason\x01run` builds the real exception:
    /// its constructor takes 5 arguments and its `str()` renders the character reprs itself,
    /// so the object only has to carry the offending run at the reported position.
    fn unicode_error(message: &str) -> Option<PyErr> {
        let (kind, payload) = message.split_once(": \u{1}")?;
        if kind != "UnicodeEncodeError" && kind != "UnicodeDecodeError" {
            return None;
        }
        let mut it = payload.split('\u{1}');
        let enc = it.next()?;
        let start: usize = it.next()?.parse().ok()?;
        let end: usize = it.next()?.parse().ok()?;
        let reason = it.next()?;
        let raw = it.next()?;
        Python::attach(|py| {
            let class = py
                .import("builtins")
                .ok()?
                .getattr(kind)
                .ok()?
                .cast_into::<pyo3::types::PyType>()
                .ok()?;
            let value = if kind == "UnicodeEncodeError" {
                let obj = " ".repeat(start) + raw;
                class.call1((enc, obj, start, end, reason)).ok()?
            } else {
                let mut bytes: Vec<u8> = vec![0u8; start];
                for i in (0..raw.len()).step_by(2) {
                    bytes.push(u8::from_str_radix(&raw[i..raw.len().min(i + 2)], 16).ok()?);
                }
                class
                    .call1((
                        enc,
                        pyo3::types::PyBytes::new(py, &bytes),
                        start,
                        end,
                        reason,
                    ))
                    .ok()?
            };
            Some(PyErr::from_value(value))
        })
    }

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

fn apply_post_prep_primer_trim_inner(
    py: Python<'_>,
    fasta_path: Bound<'_, PyAny>,
    forward_primers: Vec<String>,
    reverse_primers: Vec<String>,
    options: Option<Bound<'_, PyAny>>,
) -> Result<serde_json::Value, String> {
    let fasta_path: serde_json::Value = crate::pyconv::FromPy::from_py(&fasta_path)?;
    let options: Option<serde_json::Value> = match &options {
        None => None,
        Some(v) => Some(crate::pyconv::FromPy::from_py(v)?),
    };
    // The source calls its module-level `run_vsearch_endpoint_recheck`, so tests can
    // monkeypatch it: cross back through `primer_trim` instead of a private port copy.
    let recheck = |ctx: &port::Ctx,
                   rows: &mut Vec<serde_json::Map<String, serde_json::Value>>,
                   seqs: &[String]|
     -> Result<(i64, i64, Option<String>), String> {
        let pyerr = |e: PyErr| {
            let name = e
                .value(py)
                .get_type()
                .name()
                .map(|n| n.to_string())
                .unwrap_or_else(|_| "RuntimeError".to_string());
            format!("{name}: {e}")
        };
        let run = py
            .import("taxondbbuilder.postprep.primer_trim")
            .and_then(|m| m.getattr("run_vsearch_endpoint_recheck"))
            .map_err(pyerr)?;
        let primers = |list: &[port::Primer]| -> Vec<Py<PyAny>> {
            list.iter()
                .map(|p| pyo3::types::PyString::new(py, &p.original).into())
                .collect()
        };
        let row_objects: Vec<Py<PyAny>> = rows
            .iter()
            .map(|row| {
                crate::pyconv::ToPy::to_py(&serde_json::Value::Object(row.clone()), py)
                    .map_err(pyerr)
            })
            .collect::<Result<_, _>>()?;
        let py_rows = pyo3::types::PyList::new(py, row_objects).unwrap();
        let py_seqs = pyo3::types::PyList::new(py, seqs).unwrap();
        let out = run
            .call1((
                &py_rows,
                &py_seqs,
                primers(&ctx.forward),
                primers(&ctx.reverse),
                primers(&ctx.forward_rc),
                primers(&ctx.reverse_rc),
                ctx.recheck_min_identity,
                ctx.recheck_min_query_cov,
            ))
            .map_err(pyerr)?;
        let (attempted, rescued, error): (i64, i64, Option<String>) =
            out.extract().map_err(pyerr)?;
        for (row, item) in rows.iter_mut().zip(py_rows.iter()) {
            if let serde_json::Value::Object(updated) = crate::pyconv::FromPy::from_py(&item)? {
                *row = updated;
            }
        }
        Ok((attempted, rescued, error))
    };
    let out = port::apply_post_prep_primer_trim(
        fasta_path,
        forward_primers,
        reverse_primers,
        options,
        &recheck,
    )?;
    Ok(out)
}

/// `apply_post_prep_primer_trim`.
#[pyfunction]
#[pyo3(name = "apply_post_prep_primer_trim", signature = (fasta_path, forward_primers, reverse_primers, options=None))]
fn apply_post_prep_primer_trim(
    py: Python<'_>,
    fasta_path: Bound<'_, PyAny>,
    forward_primers: Vec<String>,
    reverse_primers: Vec<String>,
    options: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    apply_post_prep_primer_trim_inner(py, fasta_path, forward_primers, reverse_primers, options)
        .map_err(error)
        .and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(apply_post_prep_primer_trim, m)?)?;
    Ok(())
}

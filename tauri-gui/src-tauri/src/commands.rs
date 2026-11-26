use crate::config::*;
use crate::progress::*;
use crate::sidecar::{run_build_via_sidecar, resolve_sidecar_path, terminate_process_tree, BuildParams};
use crate::state::*;
use chrono::Local;
use rfd::FileDialog;
use rusqlite::{params, Connection, OpenFlags};
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{AppHandle, State};

fn completion_status(cancelled: bool, code: i32) -> &'static str {
    match (cancelled, code) {
        (true, _) => "Cancelled",
        (false, 0) => "Finished",
        (false, 3) => "PartialSuccess",
        _ => "Failed",
    }
}

static TAXONOMY_DB_DOWNLOADING: AtomicBool = AtomicBool::new(false);

/// Clears the busy flag on any exit path, including early error returns.
struct TaxonomyBusyGuard;

impl Drop for TaxonomyBusyGuard {
    fn drop(&mut self) {
        TAXONOMY_DB_DOWNLOADING.store(false, Ordering::SeqCst);
    }
}

fn take_taxonomy_busy() -> Result<TaxonomyBusyGuard, String> {
    if TAXONOMY_DB_DOWNLOADING.swap(true, Ordering::SeqCst) {
        return Err("taxonomy download is already running".to_string());
    }
    Ok(TaxonomyBusyGuard)
}

/// Ask on the main thread whether replacing an existing taxonomy.db is allowed.
/// Consent is per attempt: nothing is persisted.
fn prompt_replace(app: &AppHandle, target: &std::path::Path) -> Result<bool, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let app = app.clone();
    let description = format!(
        "{} already exists. Downloading will replace it.",
        target.display()
    );
    app.run_on_main_thread(move || {
        let answer = rfd::MessageDialog::new()
            .set_level(rfd::MessageLevel::Warning)
            .set_title("Replace existing taxonomy.db?")
            .set_description(&description)
            .set_buttons(rfd::MessageButtons::YesNo)
            .show();
        let _ = tx.send(matches!(answer, rfd::MessageDialogResult::Yes));
    })
    .map_err(|e| format!("failed to open the replace prompt: {e}"))?;
    rx.recv().map_err(|e| format!("replace prompt failed: {e}"))
}

fn run_taxonomy_fetch_build(app: &AppHandle, db_path: &std::path::Path, replace: bool) -> Result<String, String> {
    let sidecar_path = resolve_sidecar_path(app)?;
    if let Some(parent) = db_path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("failed to create {}: {e}", parent.display()))?;
    }
    let mut command = std::process::Command::new(&sidecar_path);
    command.args(["taxonomy-fetch-build", "--out-db"]).arg(db_path);
    if replace {
        command.arg("--replace");
    }
    let output = command
        .output()
        .map_err(|e| format!("failed to start {}: {e}", sidecar_path.display()))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let tail = stderr.lines().rev().take(3).collect::<Vec<_>>().join(" | ");
        return Err(format!(
            "taxonomy fetch-build failed (exit {:?}): {tail}",
            output.status.code()
        ));
    }
    Ok(db_path.to_string_lossy().into_owned())
}

#[tauri::command]
pub(crate) fn get_taxonomy_db_path(app: AppHandle) -> Result<String, String> {
    Ok(taxonomy_db_download_path(&app)?.to_string_lossy().into_owned())
}

#[tauri::command]
pub(crate) fn choose_taxonomy_db_dir(app: AppHandle) -> Result<Option<String>, String> {
    // Same exclusivity as the download: no folder swap while a build runs.
    let _busy = take_taxonomy_busy()?;
    let result = (|| {
        // A broken override must never block the repair path: use it as a hint only.
        let start = taxonomy_db_download_path(&app)
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .unwrap_or_default();
        let Some(dir) = FileDialog::new()
            .set_directory(&start)
            .set_title("Select folder for taxonomy.db")
            .pick_folder()
        else {
            return Ok(None);
        };
        let db_path = set_taxonomy_db_dir(&app, &dir)?;
        Ok(Some(db_path.to_string_lossy().into_owned()))
    })();
    result
}

/// Ok(None) means the user declined the replace prompt: nothing was written.
#[tauri::command]
pub(crate) async fn download_taxonomy_db(app: AppHandle) -> Result<Option<String>, String> {
    let result = tauri::async_runtime::spawn_blocking(move || {
        // The guard releases the flag on every exit path, including errors.
        let _busy = take_taxonomy_busy()?;
        // Resolve once: the prompt and the sidecar use this exact destination.
        let db_path = taxonomy_db_download_path(&app)?;
        let mut replace = false;
        if db_path.exists() {
            if !prompt_replace(&app, &db_path)? {
                return Ok(None);
            }
            replace = true;
        }
        run_taxonomy_fetch_build(&app, &db_path, replace).map(Some)
    })
    .await
    .map_err(|e| format!("taxonomy download task failed: {e}"))??;
    Ok(result)
}

#[tauri::command]
pub(crate) fn load_gui_config(app: AppHandle) -> Result<GuiConfig, String> {
    let path = ensure_gui_config_parent(&app)?;
    read_gui_config(&path)
}

#[tauri::command]
pub(crate) fn save_gui_config(app: AppHandle, config: GuiConfig) -> Result<(), String> {
    save_gui_config_internal(&app, &config)
}

#[tauri::command]
pub(crate) fn choose_output_directory() -> Option<String> {
    FileDialog::new()
        .pick_folder()
        .map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
pub(crate) fn choose_primer_file() -> Option<String> {
    FileDialog::new()
        .add_filter("TOML", &["toml"])
        .pick_file()
        .map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
pub(crate) fn choose_db_toml_file() -> Option<String> {
    FileDialog::new()
        .add_filter("TOML", &["toml"])
        .set_title("Select db.toml")
        .pick_file()
        .map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
pub(crate) fn search_taxonomy(
    app: AppHandle,
    query: String,
    limit: Option<usize>,
) -> Result<Vec<TaxonReferenceCandidate>, String> {
    let q = query.trim().to_lowercase();
    if q.len() < 2 {
        return Ok(Vec::new());
    }
    let max_hits = limit.unwrap_or(10).clamp(1, 50);

    let db_path = resolve_taxonomy_db_path(&app)?;
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("failed to open taxonomy.db: {e}"))?;

    let mut out: Vec<TaxonReferenceCandidate> = Vec::new();
    let limit_i64 = i64::try_from(max_hits).map_err(|e| e.to_string())?;
    let prefix = format!("{q}%");
    let contains = format!("%{q}%");

    let mut stmt = conn
        .prepare(
            "SELECT tax_id, scientific_name
             FROM taxonomy
             WHERE scientific_name LIKE ?1 COLLATE NOCASE
             ORDER BY scientific_name COLLATE NOCASE
             LIMIT ?2",
        )
        .map_err(|e| format!("failed to prepare taxonomy prefix query: {e}"))?;

    let prefix_rows = stmt
        .query_map(params![prefix, limit_i64], |row| {
            Ok(TaxonReferenceCandidate {
                tax_id: row.get::<_, i64>(0)?.to_string(),
                scientific_name: row.get::<_, String>(1)?,
            })
        })
        .map_err(|e| format!("failed to query taxonomy prefix: {e}"))?;

    let mut seen: HashSet<String> = HashSet::new();
    for row in prefix_rows {
        let item = row.map_err(|e| e.to_string())?;
        seen.insert(item.tax_id.clone());
        out.push(item);
    }

    if out.len() < max_hits {
        let remain = i64::try_from(max_hits - out.len()).map_err(|e| e.to_string())?;
        let mut stmt2 = conn
            .prepare(
                "SELECT tax_id, scientific_name
                 FROM taxonomy
                 WHERE scientific_name LIKE ?1 COLLATE NOCASE
                   AND scientific_name NOT LIKE ?2 COLLATE NOCASE
                 ORDER BY scientific_name COLLATE NOCASE
                 LIMIT ?3",
            )
            .map_err(|e| format!("failed to prepare taxonomy contains query: {e}"))?;

        let rows2 = stmt2
            .query_map(params![contains, prefix, remain], |row| {
                Ok(TaxonReferenceCandidate {
                    tax_id: row.get::<_, i64>(0)?.to_string(),
                    scientific_name: row.get::<_, String>(1)?,
                })
            })
            .map_err(|e| format!("failed to query taxonomy contains: {e}"))?;

        for row in rows2 {
            let item = row.map_err(|e| e.to_string())?;
            if seen.insert(item.tax_id.clone()) {
                out.push(item);
            }
            if out.len() >= max_hits {
                break;
            }
        }
    }

    Ok(out)
}

#[tauri::command]
pub(crate) fn import_db_toml(path: String) -> Result<ImportedDbTomlConfig, String> {
    let target = PathBuf::from(path.trim());
    if !target.exists() {
        return Err(format!("db.toml not found: {}", target.display()));
    }
    parse_db_toml_config(&target)
}

#[tauri::command]
pub(crate) fn open_path(path: String) -> Result<(), String> {
    open::that(path).map_err(|e| format!("failed to open path: {e}"))
}

#[tauri::command]
pub(crate) fn read_text_file(path: String) -> Result<String, String> {
    let target = PathBuf::from(path.trim());
    if target.as_os_str().is_empty() {
        return Err("path is required".to_string());
    }
    fs::read_to_string(&target).map_err(|e| format!("failed to read {}: {e}", target.display()))
}

#[tauri::command]
pub(crate) fn save_svg_file(content: String) -> Result<Option<String>, String> {
    if content.trim().is_empty() {
        return Err("SVG content is required".to_string());
    }
    let selected = FileDialog::new().add_filter("SVG", &["svg"]).save_file();
    let Some(path) = selected else {
        return Ok(None);
    };
    fs::write(&path, content).map_err(|e| format!("failed to write {}: {e}", path.display()))?;
    Ok(Some(path.to_string_lossy().to_string()))
}

#[tauri::command]
pub(crate) fn cancel_run(state: State<AppState>) -> Result<(), String> {
    let run = {
        let slot = state
            .run
            .lock()
            .map_err(|_| "failed to lock run state".to_string())?;
        slot.clone()
    };

    if let Some(active) = run {
        active.cancelled.store(true, Ordering::Relaxed);
        let mut guard = active
            .child
            .lock()
            .map_err(|_| "failed to lock child process".to_string())?;
        if let Some(child) = guard.as_mut() {
            terminate_process_tree(child)?;
        }
        Ok(())
    } else {
        Err("no running job".to_string())
    }
}

fn validate_taxids(taxids: &[String]) -> Result<(), String> {
    if taxids.is_empty() {
        return Err("taxids must not be empty".to_string());
    }
    if taxids.iter().any(|id| id.is_empty() || !id.bytes().all(|byte| byte.is_ascii_digit())) {
        return Err("TaxIDは半角数字のみ入力できます。".to_string());
    }
    Ok(())
}

#[tauri::command]
pub(crate) fn start_run(
    app: AppHandle,
    state: State<AppState>,
    req: RunRequest,
) -> Result<StartRunResponse, String> {
    validate_taxids(&req.taxids)?;
    if req.markers.is_empty() {
        return Err("markers must not be empty".to_string());
    }
    if req.output_root.trim().is_empty() {
        return Err("output_root is required".to_string());
    }
    let build_source = normalize_build_source(&req.source);
    validate_bold_failure_policy(&build_source, &req.on_bold_failure)?;
    if source_uses_ncbi(&build_source) && req.email.trim().is_empty() {
        return Err("email is required for ncbi/both".to_string());
    }
    let workers = resolve_worker_count(req.workers);

    {
        let slot = state
            .run
            .lock()
            .map_err(|_| "failed to lock run state".to_string())?;
        if slot.is_some() {
            return Err("another job is already running".to_string());
        }
    }

    let gui_config = GuiConfig {
        source: build_source.clone(),
        on_bold_failure: req.on_bold_failure.clone(),
        email: req.email.clone(),
        api_key: req.api_key.clone(),
        save_api_key: req.save_api_key,
        output_root: req.output_root.clone(),
        output_prefix: req.output_prefix.clone(),
        marker: req.markers.first().cloned().unwrap_or_default(),
        workers,
        ncbi_db: req.ncbi_options.db.clone(),
        ncbi_rettype: req.ncbi_options.rettype.clone(),
        ncbi_retmode: req.ncbi_options.retmode.clone(),
        ncbi_per_query: req.ncbi_options.per_query,
        ncbi_use_history: req.ncbi_options.use_history,
        ncbi_delay_sec: req.ncbi_options.delay_sec,
        output_default_header_format: req.output_options.default_header_format.clone(),
        output_mifish_header_format: req.output_options.mifish_header_format.clone(),
        output_export_formats: req.output_options.export_formats.clone(),
    };
    save_gui_config_internal(&app, &gui_config)?;

    let output_root = PathBuf::from(req.output_root.trim());
    fs::create_dir_all(&output_root)
        .map_err(|e| format!("failed to create {}: {e}", output_root.display()))?;

    let (job_id, job_dir) = prepare_job_dir(&output_root)?;
    let config_dir = job_dir.join("config");
    let gb_dir = output_root.join(".taxondbbuilder-cache").join("gb");
    let results_dir = job_dir.join("Results");
    fs::create_dir_all(&gb_dir)
        .map_err(|e| format!("failed to create {}: {e}", gb_dir.display()))?;
    fs::create_dir_all(&results_dir)
        .map_err(|e| format!("failed to create {}: {e}", results_dir.display()))?;

    let config_path = write_job_config(&req, &config_dir)?;

    let output_prefix = sanitize_file_name(if req.output_prefix.trim().is_empty() {
        DEFAULT_OUTPUT_PREFIX
    } else {
        &req.output_prefix
    });
    let output_file = results_dir.join(format!(
        "{}_{}.fasta",
        output_prefix,
        Local::now().format("%Y%m%d%H%M%S")
    ));
    let log_path = PathBuf::from(format!("{}.log", output_file.to_string_lossy()));

    let child_arc = Arc::new(Mutex::new(None));
    let cancelled = Arc::new(AtomicBool::new(false));

    {
        let mut slot = state
            .run
            .lock()
            .map_err(|_| "failed to lock run state".to_string())?;
        *slot = Some(ActiveRun {
            child: child_arc.clone(),
            cancelled: cancelled.clone(),
        });
    }

    emit_event(&app, status_event("Running"));

    let run_slot = state.run.clone();
    let app_for_thread = app.clone();
    let log_path_for_thread = log_path.clone();
    let output_file_for_thread = output_file.clone();
    let results_dir_for_thread = results_dir.clone();
    let job_dir_for_thread = job_dir.clone();
    let config_path_for_thread = config_path.clone();
    let api_key_for_thread = req.api_key.clone();
    let gb_dir_for_thread = gb_dir.clone();
    let marker_for_thread = req.markers.clone();
    let taxids_for_thread = req.taxids.clone();
    let resume_for_thread = req.resume && source_uses_ncbi(&build_source);
    let source_for_thread = build_source.clone();
    let bold_failure_for_thread = req.on_bold_failure.clone();
    let taxid_total = req.taxids.len();
    let workers_for_thread = workers;
    let output_prefix_for_thread = req.output_prefix.clone();
    let post_prep_for_thread = req.post_prep.enable;
    let post_steps_for_thread = req.post_prep.steps.clone();
    let post_primer_sets_for_thread = req.post_prep.primer_set.clone();
    let post_steps_total = if req.post_prep.enable {
        req.post_prep.steps.len().max(1)
    } else {
        0
    };

    thread::spawn(move || {
        let uses_ncbi = source_uses_ncbi(&source_for_thread);
        let uses_bold = matches!(
            source_for_thread.to_ascii_lowercase().as_str(),
            "bold" | "both"
        );

        let mut parser =
            ProgressParser::for_source(taxid_total, post_steps_total, uses_ncbi, uses_bold);

        emit_event(&app_for_thread, progress_event(&parser));

        let config_path_for_files = config_path_for_thread.clone();
        let build_params = BuildParams {
            api_key: api_key_for_thread,
            config_path: config_path_for_thread,
            taxids: taxids_for_thread,
            markers: marker_for_thread,
            source: source_for_thread,
            on_bold_failure: bold_failure_for_thread,
            output_file: output_file_for_thread.clone(),
            dump_gb_dir: gb_dir_for_thread,
            from_gb_dir: None,
            resume: resume_for_thread,
            workers: workers_for_thread,
            output_prefix: output_prefix_for_thread,
            post_prep: post_prep_for_thread,
            post_prep_steps: post_steps_for_thread,
            post_prep_primer_sets: post_primer_sets_for_thread,
        };
        let exit_code = match run_build_via_sidecar(
            &app_for_thread,
            &mut parser,
            &build_params,
            &log_path_for_thread,
            cancelled.as_ref(),
            &child_arc,
        ) {
            Ok(code) => {
                if !matches!(code, 0 | 3) && !cancelled.load(Ordering::Relaxed) {
                    emit_event(&app_for_thread, error_event(format!("taxondbbuilder sidecar exited with code {code}")));
                }
                code
            },
            Err(err) => {
                if !cancelled.load(Ordering::Relaxed) {
                    emit_event(&app_for_thread, error_event(err));
                }
                1
            }
        };

        let was_cancelled = cancelled.load(Ordering::Relaxed);
        let files = collect_files(
            &results_dir_for_thread,
            &[log_path_for_thread.clone(), config_path_for_files],
        );

        emit_event(&app_for_thread, status_event(completion_status(was_cancelled, exit_code)));

        if !was_cancelled {
            emit_event(&app_for_thread, result_event(&job_dir_for_thread, files));
        }

        if let Ok(mut slot) = run_slot.lock() {
            *slot = None;
        }
    });

    Ok(StartRunResponse {
        job_id,
        job_dir: to_abs_string(&job_dir),
        config_path: to_abs_string(&config_path),
        output_path: to_abs_string(&output_file),
        log_path: to_abs_string(&log_path),
        command: "rust-runner (integrated)".to_string(),
    })
}


#[cfg(test)]
mod taxid_tests {
    use super::validate_taxids;

    #[test]
    fn taxids_accept_only_ascii_digit_ids() {
        assert!(validate_taxids(&["30991".into(), "1756094".into()]).is_ok());
        assert!(validate_taxids(&[]).is_err());
        for invalid in ["", "Silurus", "ナマズ", "１２３", "1e3", "1.5", "-1", "12abc", " 12", "12,34"] {
            assert!(validate_taxids(&["30991".into(), invalid.into()]).is_err(), "{invalid}");
        }
    }
}

#[cfg(test)]
mod completion_tests {
    use super::completion_status;
    #[test]
    fn complete_partial_failure_and_cancel_are_distinct() {
        assert_eq!(completion_status(false, 0), "Finished");
        assert_eq!(completion_status(false, 3), "PartialSuccess");
        assert_eq!(completion_status(false, 2), "Failed");
        assert_eq!(completion_status(true, 0), "Cancelled");
        assert_eq!(completion_status(true, 3), "Cancelled");
    }
}

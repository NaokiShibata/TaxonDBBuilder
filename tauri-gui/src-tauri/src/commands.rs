use crate::config::*;
use crate::progress::*;
use crate::sidecar::{run_build_via_sidecar, terminate_process_tree, BuildParams};
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

#[tauri::command]
pub(crate) fn load_gui_config() -> Result<GuiConfig, String> {
    let path = ensure_gui_config_parent()?;
    if !path.exists() {
        return Ok(GuiConfig::default());
    }

    let text =
        fs::read_to_string(&path).map_err(|e| format!("failed to read {}: {e}", path.display()))?;
    let mut cfg: GuiConfig = serde_json::from_str(&text)
        .map_err(|e| format!("failed to parse {}: {e}", path.display()))?;
    if !cfg.save_api_key {
        cfg.api_key.clear();
    }
    Ok(cfg)
}

#[tauri::command]
pub(crate) fn save_gui_config(config: GuiConfig) -> Result<(), String> {
    save_gui_config_internal(&config)
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

#[tauri::command]
pub(crate) fn start_run(
    app: AppHandle,
    state: State<AppState>,
    req: RunRequest,
) -> Result<StartRunResponse, String> {
    if req.taxids.is_empty() {
        return Err("taxids must not be empty".to_string());
    }
    if req.markers.is_empty() {
        return Err("markers must not be empty".to_string());
    }
    if req.output_root.trim().is_empty() {
        return Err("output_root is required".to_string());
    }
    let build_source = normalize_build_source(&req.source);
    if source_uses_ncbi(&build_source) && req.email.trim().is_empty() {
        return Err("email is required for ncbi/both".to_string());
    }

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
        email: req.email.clone(),
        api_key: req.api_key.clone(),
        save_api_key: req.save_api_key,
        output_root: req.output_root.clone(),
        output_prefix: req.output_prefix.clone(),
        marker: req.markers.first().cloned().unwrap_or_default(),
        workers: req.workers,
        ncbi_db: req.ncbi_options.db.clone(),
        ncbi_rettype: req.ncbi_options.rettype.clone(),
        ncbi_retmode: req.ncbi_options.retmode.clone(),
        ncbi_per_query: req.ncbi_options.per_query,
        ncbi_use_history: req.ncbi_options.use_history,
        ncbi_delay_sec: req.ncbi_options.delay_sec,
        output_default_header_format: req.output_options.default_header_format.clone(),
        output_mifish_header_format: req.output_options.mifish_header_format.clone(),
    };
    save_gui_config_internal(&gui_config)?;

    let output_root = PathBuf::from(req.output_root.trim());
    fs::create_dir_all(&output_root)
        .map_err(|e| format!("failed to create {}: {e}", output_root.display()))?;

    let (job_id, job_dir) = prepare_job_dir(&output_root)?;
    let config_dir = job_dir.join("config");
    let gb_dir = job_dir.join("gb");
    let results_dir = job_dir.join("Results");
    fs::create_dir_all(&gb_dir)
        .map_err(|e| format!("failed to create {}: {e}", gb_dir.display()))?;
    fs::create_dir_all(&results_dir)
        .map_err(|e| format!("failed to create {}: {e}", results_dir.display()))?;

    let config_path = write_job_config(&req, &config_dir)?;

    let output_prefix = sanitize_file_name(&req.output_prefix);
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
    let gb_dir_for_thread = gb_dir.clone();
    let marker_for_thread = req.markers.clone();
    let taxids_for_thread = req.taxids.clone();
    let resume_for_thread = req.resume && source_uses_ncbi(&build_source);
    let source_for_thread = build_source.clone();
    let taxid_total = req.taxids.len();
    let workers_for_thread = req.workers;
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
        let mut parser = ProgressParser::new(taxid_total, post_steps_total);
        let build_params = BuildParams {
            config_path: config_path_for_thread,
            taxids: taxids_for_thread,
            markers: marker_for_thread,
            source: source_for_thread,
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
            Ok(()) => 0,
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
            std::slice::from_ref(&log_path_for_thread),
        );

        match (was_cancelled, exit_code) {
            (true, _) => emit_event(&app_for_thread, status_event("Cancelled")),
            (false, 0) => emit_event(&app_for_thread, status_event("Finished")),
            _ => emit_event(&app_for_thread, status_event("Failed")),
        }

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

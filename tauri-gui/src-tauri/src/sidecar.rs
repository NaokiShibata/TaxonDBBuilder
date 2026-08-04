use crate::progress::{
    emit_event, format_monitor_log_line, log_event, progress_event, tail_log_once, ProgressParser,
};
use crate::taxondb_runner::{build_params_to_args, BuildParams};
use std::env;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Manager};

const SIDECAR_NAME: &str = "taxondbbuilder";

fn add_candidate(candidates: &mut Vec<PathBuf>, path: PathBuf) {
    if !candidates.contains(&path) {
        candidates.push(path);
    }
}

fn sidecar_candidates(app: &AppHandle) -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if let Ok(path) = env::var("TAXONDBBUILDER_SIDECAR") {
        add_candidate(&mut candidates, PathBuf::from(path));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        add_candidate(
            &mut candidates,
            resource_dir.join("binaries").join(SIDECAR_NAME),
        );
        add_candidate(&mut candidates, resource_dir.join(SIDECAR_NAME));
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if let Some(repo_dir) = manifest_dir.parent().and_then(Path::parent) {
        add_candidate(&mut candidates, repo_dir.join("dist").join(SIDECAR_NAME));
    }
    add_candidate(
        &mut candidates,
        manifest_dir.join("binaries").join(SIDECAR_NAME),
    );

    if let Ok(cwd) = env::current_dir() {
        add_candidate(&mut candidates, cwd.join("dist").join(SIDECAR_NAME));
        add_candidate(&mut candidates, cwd.join("binaries").join(SIDECAR_NAME));
    }

    if let Ok(exe) = env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            add_candidate(&mut candidates, exe_dir.join("binaries").join(SIDECAR_NAME));
            add_candidate(&mut candidates, exe_dir.join(SIDECAR_NAME));
        }
    }

    candidates
}

pub(crate) fn resolve_sidecar_path(app: &AppHandle) -> Result<PathBuf, String> {
    let candidates = sidecar_candidates(app);
    if let Some(path) = candidates.iter().find(|path| path.is_file()) {
        return Ok(path.clone());
    }

    let searched = candidates
        .iter()
        .map(|path| path.display().to_string())
        .collect::<Vec<_>>()
        .join(", ");
    Err(format!(
        "taxondbbuilder sidecar not found; set TAXONDBBUILDER_SIDECAR or place the executable at one of: {searched}"
    ))
}

fn spawn_reader<R>(reader: R, tx: mpsc::Sender<String>) -> thread::JoinHandle<()>
where
    R: std::io::Read + Send + 'static,
{
    thread::spawn(move || {
        for line in BufReader::new(reader).lines().map_while(Result::ok) {
            if tx.send(line).is_err() {
                break;
            }
        }
    })
}

fn emit_sidecar_line(app: &AppHandle, parser: &mut ProgressParser, line: &str) {
    emit_event(app, log_event(format_monitor_log_line(line)));
    if parser.consume_line(line) {
        emit_event(app, progress_event(parser));
    }
}

fn take_child(child_slot: &Arc<Mutex<Option<Child>>>) -> Result<Child, String> {
    child_slot
        .lock()
        .map_err(|_| "failed to lock sidecar process".to_string())?
        .take()
        .ok_or_else(|| "sidecar process disappeared".to_string())
}

pub(crate) fn run_build_via_sidecar(
    app: &AppHandle,
    parser: &mut ProgressParser,
    params: &BuildParams,
    log_path: &Path,
    cancelled: &AtomicBool,
    child_slot: &Arc<Mutex<Option<Child>>>,
) -> Result<(), String> {
    let sidecar_path = resolve_sidecar_path(app)?;
    let mut command = Command::new(&sidecar_path);
    command
        .args(build_params_to_args(params))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = command.spawn().map_err(|error| {
        format!(
            "failed to start taxondbbuilder sidecar {}: {error}",
            sidecar_path.display()
        )
    })?;
    let stdout: ChildStdout = child
        .stdout
        .take()
        .ok_or_else(|| "sidecar stdout was not piped".to_string())?;
    let stderr: ChildStderr = child
        .stderr
        .take()
        .ok_or_else(|| "sidecar stderr was not piped".to_string())?;
    let (line_tx, line_rx) = mpsc::channel::<String>();
    let stdout_reader = spawn_reader(stdout, line_tx.clone());
    let stderr_reader = spawn_reader(stderr, line_tx);

    {
        let mut slot = child_slot
            .lock()
            .map_err(|_| "failed to lock sidecar process".to_string())?;
        *slot = Some(child);
    }

    let mut log_offset = 0;
    let status = loop {
        for line in line_rx.try_iter() {
            emit_sidecar_line(app, parser, &line);
        }
        tail_log_once(app, parser, log_path, &mut log_offset)?;

        let status = {
            let mut slot = child_slot
                .lock()
                .map_err(|_| "failed to lock sidecar process".to_string())?;
            slot.as_mut()
                .ok_or_else(|| "sidecar process disappeared".to_string())?
                .try_wait()
                .map_err(|error| format!("failed to wait for sidecar: {error}"))?
        };
        if let Some(status) = status {
            break status;
        }

        if cancelled.load(Ordering::Relaxed) {
            let mut slot = child_slot
                .lock()
                .map_err(|_| "failed to lock sidecar process".to_string())?;
            if let Some(child) = slot.as_mut() {
                child
                    .kill()
                    .map_err(|error| format!("failed to kill sidecar: {error}"))?;
            }
        }
        thread::sleep(Duration::from_millis(100));
    };

    stdout_reader
        .join()
        .map_err(|_| "sidecar stdout reader panicked".to_string())?;
    stderr_reader
        .join()
        .map_err(|_| "sidecar stderr reader panicked".to_string())?;
    for line in line_rx.try_iter() {
        emit_sidecar_line(app, parser, &line);
    }
    tail_log_once(app, parser, log_path, &mut log_offset)?;
    let _ = take_child(child_slot)?;

    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "taxondbbuilder sidecar exited with status {status}"
        ))
    }
}

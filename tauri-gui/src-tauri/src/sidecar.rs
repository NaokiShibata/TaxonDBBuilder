use crate::progress::{
    emit_event, format_monitor_log_line, log_event, progress_event, tail_log_once, ProgressParser,
};
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

#[cfg(unix)]
fn configure_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;

    command.process_group(0);
}

#[cfg(windows)]
fn configure_process_group(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    command.creation_flags(CREATE_NEW_PROCESS_GROUP);
}

pub(crate) fn terminate_process_tree(child: &mut Child) -> Result<(), String> {
    if child
        .try_wait()
        .map_err(|error| format!("failed to poll sidecar: {error}"))?
        .is_some()
    {
        return Ok(());
    }

    #[cfg(unix)]
    {
        let process_group = format!("-{}", child.id());
        let killed = Command::new("kill")
            .args(["-TERM", "--", &process_group])
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
        if killed {
            return Ok(());
        }
    }

    #[cfg(windows)]
    {
        let pid = child.id().to_string();
        let killed = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
        if killed {
            return Ok(());
        }
    }

    child
        .kill()
        .map_err(|error| format!("failed to terminate sidecar process tree: {error}"))
}

#[derive(Debug, Clone)]
pub(crate) struct BuildParams {
    pub(crate) config_path: PathBuf,
    pub(crate) taxids: Vec<String>,
    pub(crate) markers: Vec<String>,
    pub(crate) source: String,
    pub(crate) output_file: PathBuf,
    pub(crate) dump_gb_dir: PathBuf,
    pub(crate) from_gb_dir: Option<PathBuf>,
    pub(crate) resume: bool,
    pub(crate) workers: u32,
    pub(crate) output_prefix: String,
    pub(crate) post_prep: bool,
    pub(crate) post_prep_steps: Vec<String>,
    pub(crate) post_prep_primer_sets: Vec<String>,
}

/// Convert the GUI's build parameters to the Python `build` command arguments.
///
/// Configuration values such as filters, NCBI settings, and post-prep detail
/// options are written to `config_path`; only the Python CLI's command-line
/// controls belong in this vector.
pub(crate) fn build_params_to_args(params: &BuildParams) -> Vec<String> {
    let mut args = vec!["build".to_string()];
    args.extend([
        "--config".to_string(),
        params.config_path.display().to_string(),
    ]);

    for taxid in &params.taxids {
        args.extend(["--taxon".to_string(), taxid.clone()]);
    }
    for marker in &params.markers {
        args.extend(["--marker".to_string(), marker.clone()]);
    }

    args.extend(["--source".to_string(), params.source.clone()]);
    args.extend([
        "--out".to_string(),
        params.output_file.display().to_string(),
    ]);
    if !params.source.eq_ignore_ascii_case("bold") {
        args.extend([
            "--dump-gb".to_string(),
            params.dump_gb_dir.display().to_string(),
        ]);
    }
    args.extend([
        "--workers".to_string(),
        params.workers.to_string(),
        "--output-prefix".to_string(),
        params.output_prefix.clone(),
    ]);

    if let Some(from_gb_dir) = &params.from_gb_dir {
        if !params.source.eq_ignore_ascii_case("bold") {
            args.extend(["--from-gb".to_string(), from_gb_dir.display().to_string()]);
        }
    }
    if params.resume && !params.source.eq_ignore_ascii_case("bold") {
        args.push("--resume".to_string());
    }
    if params.post_prep {
        args.push("--post-prep".to_string());
        for step in &params.post_prep_steps {
            args.extend(["--post-prep-step".to_string(), step.clone()]);
        }
        for primer_set in &params.post_prep_primer_sets {
            args.extend(["--post-prep-primer-set".to_string(), primer_set.clone()]);
        }
    }

    args
}

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
        add_candidate(&mut candidates, resource_dir.join("bin").join(SIDECAR_NAME));
        add_candidate(&mut candidates, resource_dir.join(SIDECAR_NAME));
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if let Some(repo_dir) = manifest_dir.parent().and_then(Path::parent) {
        add_candidate(&mut candidates, repo_dir.join("dist").join(SIDECAR_NAME));
    }
    add_candidate(&mut candidates, manifest_dir.join("bin").join(SIDECAR_NAME));

    if let Ok(cwd) = env::current_dir() {
        add_candidate(&mut candidates, cwd.join("dist").join(SIDECAR_NAME));
        add_candidate(&mut candidates, cwd.join("bin").join(SIDECAR_NAME));
    }

    if let Ok(exe) = env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            add_candidate(&mut candidates, exe_dir.join("bin").join(SIDECAR_NAME));
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
    configure_process_group(&mut command);

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
                terminate_process_tree(child)?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::progress::progress_event;
    use std::fs;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    fn repo_path(relative: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(relative)
    }

    #[test]
    fn sidecar_from_gb_output_reaches_progress_events() {
        let sidecar = repo_path("dist/taxondbbuilder");
        if !sidecar.is_file() {
            eprintln!("skip: sidecar not found at {}", sidecar.display());
            return;
        }

        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let temp_root = std::env::temp_dir().join(format!("taxondbbuilder-sidecar-{stamp}"));
        let gb_dir = temp_root.join("from-gb");
        let output = temp_root.join("output.fasta");
        let dump_dir = temp_root.join("dump");
        fs::create_dir_all(&gb_dir).expect("from-gb directory");
        fs::copy(
            repo_path("tests/fixtures/sample.gb"),
            gb_dir.join("sample.gb"),
        )
        .expect("copy GenBank fixture");

        let params = BuildParams {
            config_path: repo_path("tests/fixtures/minimal_config.toml"),
            taxids: vec!["999".to_string()],
            markers: vec!["12s".to_string()],
            source: "ncbi".to_string(),
            output_file: output.clone(),
            dump_gb_dir: dump_dir,
            from_gb_dir: Some(gb_dir),
            resume: false,
            workers: 1,
            output_prefix: "taxondbbuilder_".to_string(),
            post_prep: false,
            post_prep_steps: Vec::new(),
            post_prep_primer_sets: Vec::new(),
        };

        let result = Command::new(&sidecar)
            .args(build_params_to_args(&params))
            .output()
            .expect("launch sidecar");
        assert!(
            result.status.success(),
            "sidecar failed: {}\n{}",
            String::from_utf8_lossy(&result.stderr),
            String::from_utf8_lossy(&result.stdout)
        );

        let mut parser = ProgressParser::new(1, 0);
        let mut event_count = 0;
        let mut events = Vec::new();
        for line in String::from_utf8_lossy(&result.stdout)
            .lines()
            .chain(String::from_utf8_lossy(&result.stderr).lines())
            .chain(
                fs::read_to_string(output.with_extension("fasta.log"))
                    .expect("sidecar log")
                    .lines(),
            )
        {
            if parser.consume_line(line) {
                event_count += 1;
                events.push(serde_json::to_value(progress_event(&parser)).expect("RunEvent JSON"));
            }
        }

        assert!(event_count > 0, "no parser events were produced");
        assert!(events.iter().any(|event| event["phase"] == "Fetch/Parse"));
        let terminal_event = events.last().expect("terminal RunEvent");
        assert_eq!(terminal_event["eventType"], "progress");
        assert_eq!(terminal_event["phase"], "Finalize");
        assert_eq!(terminal_event["percent"], 100.0);
        assert_eq!(terminal_event["metrics"]["matchedRecords"], 1);

        fs::remove_dir_all(temp_root).expect("remove integration test directory");
    }

    #[cfg(unix)]
    #[test]
    fn terminate_process_tree_kills_process_group_descendants() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let pid_file = std::env::temp_dir().join(format!("taxondbbuilder-child-pid-{stamp}"));
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg(format!("sleep 30 & echo $! > {}; wait", pid_file.display()));
        configure_process_group(&mut command);
        let mut child = command.spawn().expect("spawn process group");

        for _ in 0..100 {
            if pid_file.is_file() {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        let descendant_pid = fs::read_to_string(&pid_file)
            .expect("descendant pid file")
            .trim()
            .to_string();

        terminate_process_tree(&mut child).expect("terminate process group");
        let status = child.wait().expect("wait for process group leader");
        assert!(!status.success());

        let mut descendant_gone = false;
        for _ in 0..50 {
            let probe = Command::new("kill")
                .args(["-0", &descendant_pid])
                .status()
                .map(|status| status.success())
                .unwrap_or(false);
            if !probe {
                descendant_gone = true;
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        if !descendant_gone {
            let _ = Command::new("kill")
                .args(["-KILL", &descendant_pid])
                .status();
        }
        let _ = fs::remove_file(&pid_file);
        assert!(
            descendant_gone,
            "process-group descendant survived termination"
        );
    }
}

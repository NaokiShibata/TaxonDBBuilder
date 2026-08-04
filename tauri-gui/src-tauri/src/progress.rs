use once_cell::sync::Lazy;
use regex::Regex;
use serde::Serialize;
use std::collections::{BTreeMap, HashSet};
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Emitter};

pub(crate) const RUN_EVENT: &str = "run-event";
static RE_LOG_LINE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\s+(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+(?P<body>.*)$",
    )
    .expect("log line regex")
});
static RE_LOG_PREFIX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\s+(?:TRACE|DEBUG|INFO|WARN|ERROR)\s+",
    )
    .expect("log prefix regex")
});
static RE_QUERY_COUNT: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^# query count taxid=([^:]+):\s*(.+)$").expect("query count regex"));
static RE_FETCH_PROGRESS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^# fetch progress taxid=([^:]+):\s*(\d+)\s*/\s*(\d+)$")
        .expect("fetch progress regex")
});
static RE_BOLD_PROGRESS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^# bold progress: taxon=(.+?) phase=([a-z_]+)(?:\s+(.*))?$")
        .expect("bold progress regex")
});
static RE_BOLD_QUERY: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^# bold query: taxon=(.+?) normalized=.* specimens=(\d+)(?: .*?)? downloaded=(\d+) matched=(\d+)$",
    )
    .expect("bold query regex")
});
static RE_TOTAL_RECORDS: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^# total records:\s*(\d+)").expect("total records regex"));
static RE_MATCHED_RECORDS: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^# matched records:\s*(\d+)").expect("matched records regex"));
static RE_KEPT_BEFORE_POST: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^# kept records before post_prep:\s*(\d+)").expect("kept before post regex")
});
static RE_PRIMER_REMOVED: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"removed=(\d+)").expect("primer removed regex"));
static RE_DUP_GROUPS: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"groups=(\d+)").expect("duplicate groups regex"));
static RE_DUP_CROSS: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"cross_organism_groups=(\d+)").expect("cross organism groups regex"));
#[derive(Debug, Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RunMetrics {
    query_count_by_taxid: BTreeMap<String, u64>,
    fetch_count_by_taxid: BTreeMap<String, u64>,
    bold_specimen_count_by_taxon: BTreeMap<String, u64>,
    bold_downloaded_by_taxon: BTreeMap<String, u64>,
    bold_matched_by_taxon: BTreeMap<String, u64>,
    matched_records: Option<u64>,
    kept_records_before_post_prep: Option<u64>,
    primer_trim_removed: Option<u64>,
    length_filter_removed: Option<u64>,
    duplicate_groups: Option<u64>,
    cross_organism_groups: Option<u64>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RunEvent {
    event_type: String,
    status: Option<String>,
    phase: Option<String>,
    percent: Option<f64>,
    line: Option<String>,
    message: Option<String>,
    metrics: Option<RunMetrics>,
    files: Option<Vec<String>>,
    job_dir: Option<String>,
}
#[derive(Debug, Clone)]
pub(crate) struct ProgressParser {
    taxid_total: usize,
    query_seen: HashSet<String>,
    bold_progress_by_taxon: BTreeMap<String, f64>,
    total_records: Option<u64>,
    matched_records: Option<u64>,
    post_steps_total: usize,
    post_steps_seen: HashSet<String>,
    phase: String,
    percent: f64,
    metrics: RunMetrics,
}

impl ProgressParser {
    pub(crate) fn new(taxid_total: usize, post_steps_total: usize) -> Self {
        Self {
            taxid_total,
            query_seen: HashSet::new(),
            bold_progress_by_taxon: BTreeMap::new(),
            total_records: None,
            matched_records: None,
            post_steps_total,
            post_steps_seen: HashSet::new(),
            phase: "Query count".to_string(),
            percent: 0.0,
            metrics: RunMetrics::default(),
        }
    }

    fn consume_line(&mut self, line: &str) -> bool {
        let line = strip_log_prefix(line);
        let mut changed = false;

        if let Some(caps) = RE_QUERY_COUNT.captures(line) {
            let taxid = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim();
            let count_raw = caps.get(2).map(|m| m.as_str()).unwrap_or("").trim();
            let count = count_raw.parse::<u64>().ok();
            if !taxid.is_empty() {
                if let Some(v) = count {
                    self.metrics
                        .query_count_by_taxid
                        .insert(taxid.to_string(), v);
                }
                self.query_seen.insert(taxid.to_string());
                self.phase = "Query count".to_string();
                let total = self.taxid_total.max(1) as f64;
                self.percent = ((self.query_seen.len() as f64 / total) * 10.0).clamp(0.0, 10.0);
                changed = true;
            }
        }

        if let Some(caps) = RE_FETCH_PROGRESS.captures(line) {
            let taxid = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim();
            let fetched = caps
                .get(2)
                .and_then(|m| m.as_str().parse::<u64>().ok())
                .unwrap_or(0);
            let total = caps
                .get(3)
                .and_then(|m| m.as_str().parse::<u64>().ok())
                .unwrap_or(0);
            if !taxid.is_empty() && total > 0 {
                self.metrics
                    .query_count_by_taxid
                    .insert(taxid.to_string(), total);
                self.metrics
                    .fetch_count_by_taxid
                    .insert(taxid.to_string(), fetched.min(total));
                self.phase = "Fetch/Parse".to_string();
                changed = true;
            }
        }

        if let Some(caps) = RE_BOLD_PROGRESS.captures(line) {
            let taxon = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim();
            let phase = caps.get(2).map(|m| m.as_str()).unwrap_or("").trim();
            let detail = caps.get(3).map(|m| m.as_str()).unwrap_or("").trim();
            if !taxon.is_empty() {
                let stage_ratio = match phase {
                    "preprocess" => 0.15,
                    "summary" => 0.30,
                    "query" => 0.45,
                    "download" => 0.75,
                    "filter" => 1.00,
                    _ => 0.0,
                };
                self.bold_progress_by_taxon
                    .insert(taxon.to_string(), stage_ratio);
                self.phase = match phase {
                    "preprocess" => "BOLD Preprocess".to_string(),
                    "summary" => "BOLD Summary".to_string(),
                    "query" => "BOLD Query".to_string(),
                    "download" => "BOLD Download".to_string(),
                    "filter" => "BOLD Filter".to_string(),
                    _ => "BOLD".to_string(),
                };
                if let Some(specimens_raw) = detail
                    .split_whitespace()
                    .find_map(|token| token.strip_prefix("specimens="))
                {
                    if let Ok(specimens) = specimens_raw.parse::<u64>() {
                        self.metrics
                            .bold_specimen_count_by_taxon
                            .insert(taxon.to_string(), specimens);
                    }
                }
                if let Some(downloaded_raw) = detail
                    .split_whitespace()
                    .find_map(|token| token.strip_prefix("downloaded="))
                {
                    if let Ok(downloaded) = downloaded_raw.parse::<u64>() {
                        self.metrics
                            .bold_downloaded_by_taxon
                            .insert(taxon.to_string(), downloaded);
                    }
                }
                if let Some(matched_raw) = detail
                    .split_whitespace()
                    .find_map(|token| token.strip_prefix("matched="))
                {
                    if let Ok(matched) = matched_raw.parse::<u64>() {
                        self.metrics
                            .bold_matched_by_taxon
                            .insert(taxon.to_string(), matched);
                    }
                }
                changed = true;
            }
        }

        if let Some(caps) = RE_BOLD_QUERY.captures(line) {
            let taxon = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim();
            let specimens = caps
                .get(2)
                .and_then(|m| m.as_str().parse::<u64>().ok())
                .unwrap_or(0);
            let downloaded = caps
                .get(3)
                .and_then(|m| m.as_str().parse::<u64>().ok())
                .unwrap_or(0);
            let matched = caps
                .get(4)
                .and_then(|m| m.as_str().parse::<u64>().ok())
                .unwrap_or(0);
            if !taxon.is_empty() {
                self.metrics
                    .bold_specimen_count_by_taxon
                    .insert(taxon.to_string(), specimens);
                self.metrics
                    .bold_downloaded_by_taxon
                    .insert(taxon.to_string(), downloaded);
                self.metrics
                    .bold_matched_by_taxon
                    .insert(taxon.to_string(), matched);
                self.bold_progress_by_taxon.insert(taxon.to_string(), 1.0);
                self.phase = "BOLD Filter".to_string();
                changed = true;
            }
        }

        if let Some(caps) = RE_TOTAL_RECORDS.captures(line) {
            self.total_records = caps.get(1).and_then(|m| m.as_str().parse::<u64>().ok());
            changed = true;
        }

        if let Some(caps) = RE_MATCHED_RECORDS.captures(line) {
            let matched = caps.get(1).and_then(|m| m.as_str().parse::<u64>().ok());
            self.matched_records = matched;
            self.metrics.matched_records = matched;
            changed = true;
        }

        if let Some(caps) = RE_KEPT_BEFORE_POST.captures(line) {
            self.metrics.kept_records_before_post_prep =
                caps.get(1).and_then(|m| m.as_str().parse::<u64>().ok());
            self.phase = "Post-Prep".to_string();
            self.percent = self.percent.max(80.0);
            changed = true;
        }

        if line.starts_with("# post_prep primer trim:") {
            self.post_steps_seen.insert("primer_trim".to_string());
            self.metrics.primer_trim_removed = RE_PRIMER_REMOVED
                .captures(line)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse::<u64>().ok());
            changed = true;
        }

        if line.starts_with("# post_prep length filter:") {
            self.post_steps_seen.insert("length_filter".to_string());
            self.metrics.length_filter_removed = RE_PRIMER_REMOVED
                .captures(line)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse::<u64>().ok());
            changed = true;
        }

        if line.starts_with("# post_prep duplicate_acc_report:") {
            self.post_steps_seen.insert("duplicate_report".to_string());
            self.metrics.duplicate_groups = RE_DUP_GROUPS
                .captures(line)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse::<u64>().ok());
            self.metrics.cross_organism_groups = RE_DUP_CROSS
                .captures(line)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse::<u64>().ok());
            changed = true;
        }

        if line.starts_with("# output:") {
            self.phase = "Finalize".to_string();
            self.percent = self.percent.max(95.0);
            changed = true;
        }

        if line.starts_with("# finished:") {
            self.phase = "Finalize".to_string();
            self.percent = 100.0;
            changed = true;
        }

        if changed {
            if !self.metrics.fetch_count_by_taxid.is_empty() {
                let mut taxid_progress_sum = 0.0;
                for taxid in &self.query_seen {
                    let Some(total) = self.metrics.query_count_by_taxid.get(taxid) else {
                        continue;
                    };
                    if *total == 0 {
                        taxid_progress_sum += 1.0;
                        continue;
                    }
                    let done = *self.metrics.fetch_count_by_taxid.get(taxid).unwrap_or(&0);
                    taxid_progress_sum += (done as f64 / *total as f64).clamp(0.0, 1.0);
                }

                if taxid_progress_sum > 0.0 {
                    self.phase = "Fetch/Parse".to_string();
                    let overall_ratio =
                        (taxid_progress_sum / self.taxid_total.max(1) as f64).clamp(0.0, 1.0);
                    self.percent = self
                        .percent
                        .max((10.0 + overall_ratio * 70.0).clamp(10.0, 80.0));
                }
            }

            if !self.bold_progress_by_taxon.is_empty() {
                let bold_progress_sum: f64 = self.bold_progress_by_taxon.values().copied().sum();
                let overall_ratio =
                    (bold_progress_sum / self.taxid_total.max(1) as f64).clamp(0.0, 1.0);
                self.percent = self
                    .percent
                    .max((10.0 + overall_ratio * 70.0).clamp(10.0, 80.0));
            }

            if let (Some(total), Some(matched)) = (self.total_records, self.matched_records) {
                if total > 0 {
                    self.phase = "Fetch/Parse".to_string();
                    let ratio = (matched as f64 / total as f64).clamp(0.0, 1.0);
                    self.percent = self.percent.max((10.0 + ratio * 70.0).clamp(10.0, 80.0));
                }
            }

            if self.post_steps_total > 0 && !self.post_steps_seen.is_empty() {
                self.phase = "Post-Prep".to_string();
                let ratio = (self.post_steps_seen.len() as f64 / self.post_steps_total as f64)
                    .clamp(0.0, 1.0);
                self.percent = self.percent.max(80.0 + ratio * 15.0);
            }
        }

        changed
    }
}

pub(crate) fn emit_event(app: &AppHandle, payload: RunEvent) {
    let _ = app.emit(RUN_EVENT, payload);
}

pub(crate) fn status_event(status: &str) -> RunEvent {
    RunEvent {
        event_type: "status".to_string(),
        status: Some(status.to_string()),
        phase: None,
        percent: None,
        line: None,
        message: None,
        metrics: None,
        files: None,
        job_dir: None,
    }
}

pub(crate) fn log_event(line: String) -> RunEvent {
    RunEvent {
        event_type: "log".to_string(),
        status: None,
        phase: None,
        percent: None,
        line: Some(line),
        message: None,
        metrics: None,
        files: None,
        job_dir: None,
    }
}

pub(crate) fn format_monitor_log_line(line: &str) -> String {
    let (timestamp, level, body) = if let Some(caps) = RE_LOG_LINE.captures(line) {
        (
            caps.name("ts").map(|m| m.as_str()).unwrap_or("").trim(),
            caps.name("level").map(|m| m.as_str()).unwrap_or("").trim(),
            caps.name("body").map(|m| m.as_str()).unwrap_or("").trim(),
        )
    } else {
        ("", "", line.trim())
    };

    let shortened = if let Some(rest) = body.strip_prefix("# error:") {
        rest.trim().to_string()
    } else if let Some(rest) = body.strip_prefix("# warn:") {
        rest.trim().to_string()
    } else if let Some(rest) = body.strip_prefix("# ") {
        rest.trim().to_string()
    } else {
        body.to_string()
    };

    match (timestamp.is_empty(), level.is_empty()) {
        (false, false) => format!("{timestamp} {level:<5} {shortened}"),
        _ => shortened,
    }
}

pub(crate) fn progress_event(parser: &ProgressParser) -> RunEvent {
    RunEvent {
        event_type: "progress".to_string(),
        status: None,
        phase: Some(parser.phase.clone()),
        percent: Some(parser.percent.min(100.0)),
        line: None,
        message: None,
        metrics: Some(parser.metrics.clone()),
        files: None,
        job_dir: None,
    }
}

pub(crate) fn result_event(job_dir: &Path, files: Vec<String>) -> RunEvent {
    RunEvent {
        event_type: "result".to_string(),
        status: None,
        phase: None,
        percent: None,
        line: None,
        message: None,
        metrics: None,
        files: Some(files),
        job_dir: Some(job_dir.to_string_lossy().to_string()),
    }
}

pub(crate) fn error_event(message: String) -> RunEvent {
    RunEvent {
        event_type: "error".to_string(),
        status: None,
        phase: None,
        percent: None,
        line: None,
        message: Some(message),
        metrics: None,
        files: None,
        job_dir: None,
    }
}
pub(crate) fn strip_log_prefix(line: &str) -> &str {
    if let Some(matched) = RE_LOG_PREFIX.find(line) {
        &line[matched.end()..]
    } else {
        line
    }
}
pub(crate) fn collect_files(results_dir: &Path, extra: &[PathBuf]) -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(entries) = fs::read_dir(results_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                out.push(path.to_string_lossy().to_string());
            }
        }
    }
    for p in extra {
        if p.exists() {
            out.push(p.to_string_lossy().to_string());
        }
    }
    out.sort();
    out.dedup();
    out
}

pub(crate) fn tail_log_once(
    app: &AppHandle,
    parser: &mut ProgressParser,
    log_path: &Path,
    offset: &mut u64,
) -> Result<(), String> {
    if !log_path.exists() {
        return Ok(());
    }

    let mut file = File::open(log_path)
        .map_err(|e| format!("failed to open log {}: {e}", log_path.display()))?;
    file.seek(SeekFrom::Start(*offset))
        .map_err(|e| format!("failed to seek log {}: {e}", log_path.display()))?;

    let mut data = Vec::new();
    file.read_to_end(&mut data)
        .map_err(|e| format!("failed to read log {}: {e}", log_path.display()))?;

    if data.is_empty() {
        return Ok(());
    }

    *offset += data.len() as u64;
    let text = String::from_utf8_lossy(&data);

    for line in text.lines() {
        emit_event(app, log_event(format_monitor_log_line(line)));
        if parser.consume_line(line) {
            emit_event(app, progress_event(parser));
        }
    }

    Ok(())
}

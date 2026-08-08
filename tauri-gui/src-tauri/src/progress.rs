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

static RE_QUERY_START: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^# query taxid=([^:]+):\s*(.+)$").expect("query start regex"));

static RE_NETWORK_RETRY: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^#?\s*(?:esearch|efetch)\s+retry\b").expect("network retry regex"));

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
static RE_MSA_TREE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^# post_prep msa_tree: (?:mode=(?P<mode>\S+) )?status=(?P<status>\S+) taxa=(?P<taxa>\d+) msa=(?P<msa>\S+) tree=(?P<tree>\S+)$",
    )
        .expect("msa tree regex")
});
#[derive(Debug, Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RunMetrics {
    active_taxid: Option<String>,
    network_retries: u64,

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
    msa_tree_status: Option<String>,
    msa_tree_taxa: Option<u64>,
    msa_tree_msa_path: Option<String>,
    msa_tree_tree_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Default)]
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
    uses_ncbi: bool,
    uses_bold: bool,
}

impl ProgressParser {
    #[cfg(test)]
    pub(crate) fn new(taxid_total: usize, post_steps_total: usize) -> Self {
        Self::for_source(taxid_total, post_steps_total, true, false)
    }

    pub(crate) fn for_source(
        taxid_total: usize,
        post_steps_total: usize,
        uses_ncbi: bool,
        uses_bold: bool,
    ) -> Self {
        Self {
            taxid_total,
            query_seen: HashSet::new(),
            bold_progress_by_taxon: BTreeMap::new(),
            total_records: None,
            matched_records: None,
            post_steps_total,
            post_steps_seen: HashSet::new(),
            phase: "Preparing".to_string(),
            percent: 0.0,
            metrics: RunMetrics::default(),
            uses_ncbi,
            uses_bold,
        }
    }

    pub(crate) fn consume_line(&mut self, line: &str) -> bool {
        let line = strip_log_prefix(line);

        let mut changed = false;
        let mut fetch_progress_changed = false;
        let mut bold_progress_changed = false;
        let mut record_progress_changed = false;

        if let Some(caps) = RE_QUERY_START.captures(line) {
            let taxid = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim();

            if !taxid.is_empty() {
                self.metrics.active_taxid = Some(taxid.to_string());
                self.phase = "NCBI Query".to_string();
                self.percent = self.percent.max(2.0);
                changed = true;
            }
        }

        if RE_NETWORK_RETRY.is_match(line) {
            self.metrics.network_retries = self.metrics.network_retries.saturating_add(1);

            self.phase = "NCBI Query (retry)".to_string();
            self.percent = self.percent.max(2.0);
            changed = true;
        }

        if let Some(caps) = RE_QUERY_COUNT.captures(line) {
            let taxid = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim();

            let count_raw = caps.get(2).map(|m| m.as_str()).unwrap_or("").trim();

            let count = count_raw.parse::<u64>().ok();

            if !taxid.is_empty() {
                if let Some(value) = count {
                    self.metrics
                        .query_count_by_taxid
                        .insert(taxid.to_string(), value);
                }

                self.metrics.active_taxid = Some(taxid.to_string());
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
                self.phase = "NCBI Fetch/Parse".to_string();

                fetch_progress_changed = true;
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
                bold_progress_changed = true;
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
                bold_progress_changed = true;
                changed = true;
            }
        }

        if let Some(caps) = RE_TOTAL_RECORDS.captures(line) {
            self.total_records = caps.get(1).and_then(|m| m.as_str().parse::<u64>().ok());

            record_progress_changed = true;
            changed = true;
        }

        if let Some(caps) = RE_MATCHED_RECORDS.captures(line) {
            let matched = caps.get(1).and_then(|m| m.as_str().parse::<u64>().ok());

            self.matched_records = matched;
            self.metrics.matched_records = matched;

            record_progress_changed = true;
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

        if let Some(caps) = RE_MSA_TREE.captures(line) {
            self.post_steps_seen.insert("msa_tree".to_string());
            self.metrics.msa_tree_status = caps.name("status").map(|m| m.as_str().to_string());
            self.metrics.msa_tree_taxa = caps
                .name("taxa")
                .and_then(|m| m.as_str().parse::<u64>().ok());
            let msa_path = caps.name("msa").map(|m| m.as_str()).unwrap_or("none");
            let tree_path = caps.name("tree").map(|m| m.as_str()).unwrap_or("none");
            self.metrics.msa_tree_msa_path = (msa_path != "none").then(|| msa_path.to_string());
            self.metrics.msa_tree_tree_path = (tree_path != "none").then(|| tree_path.to_string());
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
            if fetch_progress_changed {
                let mut completed = 0.0;

                for (taxid, total) in &self.metrics.query_count_by_taxid {
                    if *total == 0 {
                        completed += 1.0;
                        continue;
                    }

                    let fetched = *self.metrics.fetch_count_by_taxid.get(taxid).unwrap_or(&0);

                    completed += (fetched as f64 / *total as f64).clamp(0.0, 1.0);
                }

                let overall_ratio = (completed / self.taxid_total.max(1) as f64).clamp(0.0, 1.0);

                let (base, span) = if self.uses_bold {
                    // source=both
                    (5.0, 45.0)
                } else {
                    // source=ncbi
                    (10.0, 70.0)
                };

                self.percent = self
                    .percent
                    .max((base + overall_ratio * span).clamp(base, base + span));
            }

            if bold_progress_changed {
                let completed: f64 = self.bold_progress_by_taxon.values().copied().sum();

                let overall_ratio = (completed / self.taxid_total.max(1) as f64).clamp(0.0, 1.0);

                let (base, span) = if self.uses_ncbi {
                    // source=both
                    (50.0, 30.0)
                } else {
                    // source=bold
                    (10.0, 70.0)
                };

                self.percent = self
                    .percent
                    .max((base + overall_ratio * span).clamp(base, base + span));
            }

            if record_progress_changed {
                if let (Some(total), Some(matched)) = (self.total_records, self.matched_records) {
                    if total > 0 {
                        self.phase = "Record processing".to_string();

                        let ratio = (matched as f64 / total as f64).clamp(0.0, 1.0);

                        let (base, span) = if self.uses_bold {
                            // source=both
                            (70.0, 10.0)
                        } else {
                            // source=ncbi
                            (10.0, 70.0)
                        };

                        self.percent = self
                            .percent
                            .max((base + ratio * span).clamp(base, base + span));
                    }
                }
            }

            if self.post_steps_total > 0 && !self.post_steps_seen.is_empty() {
                self.phase = "Post-Prep".to_string();
                let ratio = (self.post_steps_seen.len() as f64 / self.post_steps_total as f64)
                    .clamp(0.0, 1.0);
                self.percent = self.percent.max(80.0 + ratio * 15.0);
            }

            // Finalization must win over the generic record-ratio updates
            // above when the terminal log lines arrive after the counters.
            if line.starts_with("# finished:") {
                self.phase = "Finalize".to_string();
                self.percent = 100.0;
            } else if line.starts_with("# output:") {
                self.phase = "Finalize".to_string();
                self.percent = self.percent.max(95.0);
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
        ..Default::default()
    }
}

pub(crate) fn log_event(line: String) -> RunEvent {
    RunEvent {
        event_type: "log".to_string(),
        line: Some(line),
        ..Default::default()
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
        phase: Some(parser.phase.clone()),
        percent: Some(parser.percent.min(100.0)),
        metrics: Some(parser.metrics.clone()),
        ..Default::default()
    }
}

pub(crate) fn result_event(job_dir: &Path, files: Vec<String>) -> RunEvent {
    RunEvent {
        event_type: "result".to_string(),
        files: Some(files),
        job_dir: Some(job_dir.to_string_lossy().to_string()),
        ..Default::default()
    }
}

pub(crate) fn error_event(message: String) -> RunEvent {
    RunEvent {
        event_type: "error".to_string(),
        message: Some(message),
        ..Default::default()
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
    pending: &mut Vec<u8>,
) -> Result<(), String> {
    tail_log_once_impl(parser, log_path, offset, pending, |event| {
        emit_event(app, event);
    })
}

fn tail_log_once_impl(
    parser: &mut ProgressParser,
    log_path: &Path,
    offset: &mut u64,
    pending: &mut Vec<u8>,
    mut emit: impl FnMut(RunEvent),
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
    pending.extend_from_slice(&data);

    let Some(split_at) = pending.iter().rposition(|&b| b == b'\n').map(|i| i + 1) else {
        return Ok(());
    };

    let complete: Vec<u8> = pending.drain(..split_at).collect();
    let text = String::from_utf8_lossy(&complete);

    for line in text.lines() {
        emit(log_event(format_monitor_log_line(line)));
        if parser.consume_line(line) {
            emit(progress_event(parser));
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn golden(name: &str) -> String {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/golden")
            .join(name);
        fs::read_to_string(path).expect("golden log")
    }

    fn consume_golden(parser: &mut ProgressParser, name: &str) {
        for line in golden(name).lines() {
            parser.consume_line(line);
        }
    }

    #[test]
    fn ncbi_and_postprep_goldens_match_parser_patterns() {
        let mut ncbi = ProgressParser::new(1, 0);
        consume_golden(&mut ncbi, "build-ncbi.log.golden");
        assert!(ncbi.query_seen.contains("999"));
        assert_eq!(ncbi.total_records, Some(2));
        assert_eq!(ncbi.metrics.matched_records, Some(1));
        assert_eq!(ncbi.phase, "Finalize");
        assert_eq!(ncbi.percent, 100.0);

        let mut postprep = ProgressParser::new(1, 3);
        consume_golden(&mut postprep, "build-postprep.log.golden");
        assert_eq!(postprep.metrics.kept_records_before_post_prep, Some(3));
        assert_eq!(postprep.metrics.primer_trim_removed, Some(0));
        assert_eq!(postprep.metrics.length_filter_removed, Some(0));
        assert_eq!(postprep.metrics.duplicate_groups, Some(1));
        assert_eq!(postprep.metrics.cross_organism_groups, Some(1));
        assert_eq!(postprep.post_steps_seen.len(), 3);
        assert_eq!(postprep.phase, "Finalize");
        assert_eq!(postprep.percent, 100.0);
    }

    #[test]
    fn bold_and_both_goldens_match_bold_query_pattern() {
        let mut bold = ProgressParser::new(1, 0);
        consume_golden(&mut bold, "build-bold.log.golden");
        assert_eq!(
            bold.metrics
                .bold_specimen_count_by_taxon
                .get("Testus alpha"),
            Some(&3)
        );
        assert_eq!(
            bold.metrics.bold_downloaded_by_taxon.get("Testus alpha"),
            Some(&2)
        );
        assert_eq!(
            bold.metrics.bold_matched_by_taxon.get("Testus alpha"),
            Some(&1)
        );

        let mut both = ProgressParser::new(1, 0);
        consume_golden(&mut both, "build-both.log.golden");
        assert!(both.query_seen.contains("999"));
        assert_eq!(
            both.metrics.bold_matched_by_taxon.get("Testus alpha"),
            Some(&2)
        );
    }

    #[test]
    fn parser_accepts_timestamp_fetch_and_bold_progress_lines() {
        let mut parser = ProgressParser::new(1, 0);
        assert!(
            parser.consume_line("2026-08-04T12:00:00.000+09:00 INFO  # query count taxid=999: 4")
        );
        assert!(parser.consume_line("# fetch progress taxid=999: 2/4"));
        assert_eq!(parser.metrics.fetch_count_by_taxid.get("999"), Some(&2));
        assert!(parser.consume_line(
            "# bold progress: taxon=Testus alpha phase=download specimens=3 downloaded=2 matched=1"
        ));
        assert_eq!(
            parser
                .metrics
                .bold_specimen_count_by_taxon
                .get("Testus alpha"),
            Some(&3)
        );
        assert_eq!(parser.phase, "BOLD Download");
    }

    #[test]
    fn tail_log_preserves_incomplete_line_across_polls() {
        use std::io::Write;
        use std::time::{SystemTime, UNIX_EPOCH};

        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let log_path =
            std::env::temp_dir().join(format!("taxondbbuilder-progress-tail-{stamp}.log"));
        let mut file = File::create(&log_path).expect("create temporary log");
        file.write_all(b"# query count taxid=999: 4\n# fetch progress taxid=999: 2/")
            .expect("write first log chunk");

        let mut parser = ProgressParser::new(1, 0);
        let mut offset = 0;
        let mut pending = Vec::new();
        let mut emitted_lines = Vec::new();

        tail_log_once_impl(&mut parser, &log_path, &mut offset, &mut pending, |event| {
            if event.event_type == "log" {
                emitted_lines.push(event.line.expect("log event line"));
            }
        })
        .expect("tail first log chunk");
        assert_eq!(parser.metrics.query_count_by_taxid.get("999"), Some(&4));
        assert!(!parser.metrics.fetch_count_by_taxid.contains_key("999"));

        let mut file = fs::OpenOptions::new()
            .append(true)
            .open(&log_path)
            .expect("reopen temporary log");
        file.write_all(b"2000\n").expect("write second log chunk");

        tail_log_once_impl(&mut parser, &log_path, &mut offset, &mut pending, |event| {
            if event.event_type == "log" {
                emitted_lines.push(event.line.expect("log event line"));
            }
        })
        .expect("tail completed log line");
        assert_eq!(parser.metrics.fetch_count_by_taxid.get("999"), Some(&2));
        assert_eq!(
            emitted_lines,
            vec![
                "query count taxid=999: 4".to_string(),
                "fetch progress taxid=999: 2/2000".to_string(),
            ]
        );

        fs::remove_file(log_path).expect("remove temporary log");
    }
}

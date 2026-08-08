use chrono::Local;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

pub(crate) const CONFIG_DIR_NAME: &str = ".taxondb_gui";
pub(crate) const CONFIG_FILE_NAME: &str = "config.json";
pub(crate) const DEFAULT_NCBI_DB: &str = "nucleotide";
pub(crate) const DEFAULT_NCBI_RETTYPE: &str = "gb";
pub(crate) const DEFAULT_NCBI_RETMODE: &str = "text";
pub(crate) const DEFAULT_NCBI_PER_QUERY: u32 = 100;
pub(crate) const DEFAULT_BUILD_SOURCE: &str = "ncbi";
pub(crate) const DEFAULT_OUTPUT_HEADER_FORMAT: &str =
    "{acc_id}|{organism}|{marker}|{label}|{type}|{loc}|{strand}";
pub(crate) const DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT: &str = "{db}|{acc_id}|{organism}";
pub(crate) const DEFAULT_MSA_TREE_MODE: &str = "disabled";

pub(crate) static MARKERS_TEMPLATE: &str =
    include_str!("../../resources/templates/markers_mitogenome.toml");
pub(crate) static PRIMERS_TEMPLATE: &str = include_str!("../../resources/templates/primers.toml");
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(default)]
pub(crate) struct GuiConfig {
    pub(crate) source: String,
    pub(crate) email: String,
    pub(crate) api_key: String,
    pub(crate) save_api_key: bool,
    pub(crate) output_root: String,
    pub(crate) output_prefix: String,
    pub(crate) marker: String,
    pub(crate) workers: u32,
    pub(crate) ncbi_db: String,
    pub(crate) ncbi_rettype: String,
    pub(crate) ncbi_retmode: String,
    pub(crate) ncbi_per_query: u32,
    pub(crate) ncbi_use_history: bool,
    pub(crate) ncbi_delay_sec: Option<f64>,
    pub(crate) output_default_header_format: String,
    pub(crate) output_mifish_header_format: String,
}

impl Default for GuiConfig {
    fn default() -> Self {
        Self {
            source: DEFAULT_BUILD_SOURCE.to_string(),
            email: String::new(),
            api_key: String::new(),
            save_api_key: false,
            output_root: String::new(),
            output_prefix: "MiFish".to_string(),
            marker: "12s".to_string(),
            workers: 8,
            ncbi_db: DEFAULT_NCBI_DB.to_string(),
            ncbi_rettype: DEFAULT_NCBI_RETTYPE.to_string(),
            ncbi_retmode: DEFAULT_NCBI_RETMODE.to_string(),
            ncbi_per_query: DEFAULT_NCBI_PER_QUERY,
            ncbi_use_history: true,
            ncbi_delay_sec: None,
            output_default_header_format: DEFAULT_OUTPUT_HEADER_FORMAT.to_string(),
            output_mifish_header_format: DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT.to_string(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct FiltersInput {
    pub(crate) mitochondrion: bool,
    pub(crate) ddbj_embl_genbank: bool,
    pub(crate) biomol_genomic: bool,
    pub(crate) length_min: Option<u32>,
    pub(crate) length_max: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PostPrepInput {
    pub(crate) enable: bool,
    #[serde(default, rename = "msaTree", alias = "msaTreeEnable")]
    pub(crate) msa_tree_enable: bool,
    #[serde(default = "default_msa_tree_mode")]
    pub(crate) msa_tree_mode: String,
    pub(crate) primer_file: String,
    pub(crate) primer_set: Vec<String>,
    pub(crate) steps: Vec<String>,
    pub(crate) sequence_length_min: Option<u32>,
    pub(crate) sequence_length_max: Option<u32>,
    pub(crate) primer_max_mismatch: Option<u32>,
    pub(crate) primer_max_error_rate: Option<f64>,
    pub(crate) primer_min_overlap_bp: Option<u32>,
    pub(crate) primer_min_overlap_ratio: Option<f64>,
    pub(crate) primer_end_max_offset: Option<u32>,
    pub(crate) primer_trim_mode: Option<String>,
    pub(crate) primer_keep_retained_fasta: Option<bool>,
    pub(crate) primer_iter_enable: Option<bool>,
    pub(crate) primer_iter_max_rounds: Option<u32>,
    pub(crate) primer_iter_stop_delta: Option<f64>,
    pub(crate) primer_iter_target_conf: Option<f64>,
    pub(crate) primer_recheck_tool: Option<String>,
    pub(crate) primer_recheck_min_identity: Option<f64>,
    pub(crate) primer_recheck_min_query_cov: Option<f64>,
    pub(crate) primer_sidecar_format: Option<String>,
}

fn default_msa_tree_mode() -> String {
    DEFAULT_MSA_TREE_MODE.to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(default)]
pub(crate) struct NcbiOptionsInput {
    pub(crate) db: String,
    pub(crate) rettype: String,
    pub(crate) retmode: String,
    pub(crate) per_query: u32,
    pub(crate) use_history: bool,
    pub(crate) delay_sec: Option<f64>,
}

impl Default for NcbiOptionsInput {
    fn default() -> Self {
        Self {
            db: DEFAULT_NCBI_DB.to_string(),
            rettype: DEFAULT_NCBI_RETTYPE.to_string(),
            retmode: DEFAULT_NCBI_RETMODE.to_string(),
            per_query: DEFAULT_NCBI_PER_QUERY,
            use_history: true,
            delay_sec: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(default)]
pub(crate) struct OutputOptionsInput {
    pub(crate) default_header_format: String,
    pub(crate) mifish_header_format: String,
}

impl Default for OutputOptionsInput {
    fn default() -> Self {
        Self {
            default_header_format: DEFAULT_OUTPUT_HEADER_FORMAT.to_string(),
            mifish_header_format: DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT.to_string(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RunRequest {
    pub(crate) taxids: Vec<String>,
    pub(crate) markers: Vec<String>,
    #[serde(default = "default_build_source")]
    pub(crate) source: String,
    pub(crate) output_prefix: String,
    pub(crate) output_root: String,
    pub(crate) email: String,
    pub(crate) api_key: String,
    pub(crate) save_api_key: bool,
    pub(crate) filters: FiltersInput,
    pub(crate) post_prep: PostPrepInput,
    pub(crate) workers: u32,
    pub(crate) resume: bool,
    #[serde(default)]
    pub(crate) ncbi_options: NcbiOptionsInput,
    #[serde(default)]
    pub(crate) output_options: OutputOptionsInput,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ImportedDbTomlConfig {
    pub(crate) source_path: String,
    pub(crate) source: String,
    pub(crate) email: String,
    pub(crate) api_key: String,
    pub(crate) ncbi_options: NcbiOptionsInput,
    pub(crate) output_options: OutputOptionsInput,
    pub(crate) filters: FiltersInput,
    pub(crate) post_prep: PostPrepInput,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct TaxonReferenceCandidate {
    pub(crate) tax_id: String,
    pub(crate) scientific_name: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct StartRunResponse {
    pub(crate) job_id: String,
    pub(crate) job_dir: String,
    pub(crate) config_path: String,
    pub(crate) output_path: String,
    pub(crate) log_path: String,
    pub(crate) command: String,
}
pub(crate) fn to_abs_string(path: &Path) -> String {
    path.to_string_lossy().to_string()
}

pub(crate) fn default_build_source() -> String {
    DEFAULT_BUILD_SOURCE.to_string()
}

pub(crate) fn normalize_build_source(raw: &str) -> String {
    match raw.trim().to_ascii_lowercase().as_str() {
        "bold" => "bold".to_string(),
        "both" => "both".to_string(),
        _ => "ncbi".to_string(),
    }
}

pub(crate) fn source_uses_ncbi(source: &str) -> bool {
    normalize_build_source(source) != "bold"
}

pub(crate) fn gui_config_path() -> Result<PathBuf, String> {
    let home = dirs::home_dir().ok_or_else(|| "HOME directory not found".to_string())?;
    Ok(home.join(CONFIG_DIR_NAME).join(CONFIG_FILE_NAME))
}

pub(crate) fn ensure_gui_config_parent() -> Result<PathBuf, String> {
    let path = gui_config_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create {}: {e}", parent.display()))?;
    }
    Ok(path)
}

pub(crate) fn sanitize_file_name(raw: &str) -> String {
    let mut s = raw.trim().replace(' ', "_");
    s = s
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if s.is_empty() {
        "taxondbbuilder".to_string()
    } else {
        s
    }
}

pub(crate) fn prepare_job_dir(output_root: &Path) -> Result<(String, PathBuf), String> {
    let date_dir = output_root.join(Local::now().format("%Y%m%d").to_string());
    fs::create_dir_all(&date_dir)
        .map_err(|e| format!("failed to create {}: {e}", date_dir.display()))?;

    for idx in 1..10000 {
        let candidate = date_dir.join(format!("job{idx}"));
        if !candidate.exists() {
            fs::create_dir_all(&candidate)
                .map_err(|e| format!("failed to create {}: {e}", candidate.display()))?;
            return Ok((
                format!("{}-job{idx}", Local::now().format("%Y%m%d")),
                candidate,
            ));
        }
    }

    Err("could not allocate job directory".to_string())
}

pub(crate) fn toml_quote(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

pub(crate) fn write_job_config(req: &RunRequest, config_dir: &Path) -> Result<PathBuf, String> {
    fs::create_dir_all(config_dir)
        .map_err(|e| format!("failed to create {}: {e}", config_dir.display()))?;

    let markers_path = config_dir.join("markers_mitogenome.toml");
    fs::write(&markers_path, MARKERS_TEMPLATE)
        .map_err(|e| format!("failed to write {}: {e}", markers_path.display()))?;

    let bundled_primers_path = config_dir.join("primers.toml");
    fs::write(&bundled_primers_path, PRIMERS_TEMPLATE)
        .map_err(|e| format!("failed to write {}: {e}", bundled_primers_path.display()))?;

    let mut filters: Vec<String> = Vec::new();
    if req.filters.mitochondrion {
        filters.push("mitochondrion".to_string());
    }
    if req.filters.ddbj_embl_genbank {
        filters.push("ddbj_embl_genbank".to_string());
    }

    let mut props: Vec<String> = Vec::new();
    if req.filters.biomol_genomic {
        props.push("biomol_genomic".to_string());
    }

    let primer_file = if req.post_prep.primer_file.trim().is_empty() {
        bundled_primers_path.to_string_lossy().to_string()
    } else {
        req.post_prep.primer_file.trim().to_string()
    };

    let ncbi_db = if req.ncbi_options.db.trim().is_empty() {
        DEFAULT_NCBI_DB.to_string()
    } else {
        req.ncbi_options.db.trim().to_string()
    };
    let ncbi_rettype = if req.ncbi_options.rettype.trim().is_empty() {
        DEFAULT_NCBI_RETTYPE.to_string()
    } else {
        req.ncbi_options.rettype.trim().to_string()
    };
    let ncbi_retmode = if req.ncbi_options.retmode.trim().is_empty() {
        DEFAULT_NCBI_RETMODE.to_string()
    } else {
        req.ncbi_options.retmode.trim().to_string()
    };
    let ncbi_per_query = req.ncbi_options.per_query.max(1);

    let output_default_header_format = if req.output_options.default_header_format.trim().is_empty()
    {
        DEFAULT_OUTPUT_HEADER_FORMAT.to_string()
    } else {
        req.output_options.default_header_format.trim().to_string()
    };
    let output_mifish_header_format = if req.output_options.mifish_header_format.trim().is_empty() {
        DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT.to_string()
    } else {
        req.output_options.mifish_header_format.trim().to_string()
    };
    let source = normalize_build_source(&req.source);

    let mut toml = String::new();
    if source_uses_ncbi(&source) {
        toml.push_str("[ncbi]\n");
        toml.push_str(&format!("email = {}\n", toml_quote(req.email.trim())));
        if !req.api_key.trim().is_empty() {
            toml.push_str(&format!("api_key = {}\n", toml_quote(req.api_key.trim())));
        }
        toml.push_str(&format!("db = {}\n", toml_quote(&ncbi_db)));
        toml.push_str(&format!("rettype = {}\n", toml_quote(&ncbi_rettype)));
        toml.push_str(&format!("retmode = {}\n", toml_quote(&ncbi_retmode)));
        toml.push_str(&format!("per_query = {}\n", ncbi_per_query));
        toml.push_str(&format!("use_history = {}\n", req.ncbi_options.use_history));
        if let Some(delay_sec) = req.ncbi_options.delay_sec {
            if delay_sec > 0.0 {
                toml.push_str(&format!("delay_sec = {}\n", delay_sec));
            }
        }
        toml.push('\n');
    }

    toml.push_str("[output]\n");
    toml.push_str(&format!(
        "default_header_format = {}\n\n",
        toml_quote(&output_default_header_format)
    ));
    toml.push_str("[output.header_formats]\n");
    toml.push_str(&format!(
        "mifish_pipeline = {}\n\n",
        toml_quote(&output_mifish_header_format)
    ));

    toml.push_str("[taxon]\n");
    toml.push_str("noexp = false\n\n");

    toml.push_str("[markers]\n");
    toml.push_str("file = \"markers_mitogenome.toml\"\n\n");

    toml.push_str("[filters]\n");
    if !filters.is_empty() {
        let values = filters
            .iter()
            .map(|v| toml_quote(v))
            .collect::<Vec<_>>()
            .join(", ");
        toml.push_str(&format!("filter = [{}]\n", values));
    }
    if !props.is_empty() {
        let values = props
            .iter()
            .map(|v| toml_quote(v))
            .collect::<Vec<_>>()
            .join(", ");
        toml.push_str(&format!("properties = [{}]\n", values));
    }
    if let Some(v) = req.filters.length_min {
        toml.push_str(&format!("sequence_length_min = {}\n", v));
    }
    if let Some(v) = req.filters.length_max {
        toml.push_str(&format!("sequence_length_max = {}\n", v));
    }
    toml.push('\n');

    toml.push_str("[post_prep]\n");
    let msa_tree_mode = match req.post_prep.msa_tree_mode.trim() {
        "combined" => "combined",
        "per_taxid" => "per_taxid",
        "disabled" => "disabled",
        _ if req.post_prep.msa_tree_enable => "combined",
        _ => "disabled",
    };
    toml.push_str(&format!(
        "msa_tree_enable = {}\n",
        msa_tree_mode != "disabled"
    ));
    toml.push_str(&format!("msa_tree_mode = {}\n", toml_quote(msa_tree_mode)));
    toml.push_str(&format!("primer_file = {}\n", toml_quote(&primer_file)));
    if !req.post_prep.primer_set.is_empty() {
        let sets = req
            .post_prep
            .primer_set
            .iter()
            .map(|s| toml_quote(s))
            .collect::<Vec<_>>()
            .join(", ");
        toml.push_str(&format!("primer_set = [{}]\n", sets));
    }
    if let Some(v) = req.post_prep.sequence_length_min {
        toml.push_str(&format!("sequence_length_min = {}\n", v));
    }
    if let Some(v) = req.post_prep.sequence_length_max {
        toml.push_str(&format!("sequence_length_max = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_max_mismatch {
        toml.push_str(&format!("primer_max_mismatch = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_max_error_rate {
        toml.push_str(&format!("primer_max_error_rate = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_min_overlap_bp {
        toml.push_str(&format!("primer_min_overlap_bp = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_min_overlap_ratio {
        toml.push_str(&format!("primer_min_overlap_ratio = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_end_max_offset {
        toml.push_str(&format!("primer_end_max_offset = {}\n", v));
    }
    if let Some(v) = &req.post_prep.primer_trim_mode {
        if !v.trim().is_empty() {
            toml.push_str(&format!("primer_trim_mode = {}\n", toml_quote(v.trim())));
        }
    }
    if let Some(v) = req.post_prep.primer_keep_retained_fasta {
        toml.push_str(&format!("primer_keep_retained_fasta = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_iter_enable {
        toml.push_str(&format!("primer_iter_enable = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_iter_max_rounds {
        toml.push_str(&format!("primer_iter_max_rounds = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_iter_stop_delta {
        toml.push_str(&format!("primer_iter_stop_delta = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_iter_target_conf {
        toml.push_str(&format!("primer_iter_target_conf = {}\n", v));
    }
    if let Some(v) = &req.post_prep.primer_recheck_tool {
        if !v.trim().is_empty() {
            toml.push_str(&format!("primer_recheck_tool = {}\n", toml_quote(v.trim())));
        }
    }
    if let Some(v) = req.post_prep.primer_recheck_min_identity {
        toml.push_str(&format!("primer_recheck_min_identity = {}\n", v));
    }
    if let Some(v) = req.post_prep.primer_recheck_min_query_cov {
        toml.push_str(&format!("primer_recheck_min_query_cov = {}\n", v));
    }
    if let Some(v) = &req.post_prep.primer_sidecar_format {
        if !v.trim().is_empty() {
            toml.push_str(&format!(
                "primer_sidecar_format = {}\n",
                toml_quote(v.trim())
            ));
        }
    }

    let config_path = config_dir.join("db.toml");
    fs::write(&config_path, toml)
        .map_err(|e| format!("failed to write {}: {e}", config_path.display()))?;

    Ok(config_path)
}
pub(crate) fn save_gui_config_internal(config: &GuiConfig) -> Result<(), String> {
    let path = ensure_gui_config_parent()?;
    let mut saved = config.clone();
    if !saved.save_api_key {
        saved.api_key.clear();
    }
    let json = serde_json::to_string_pretty(&saved)
        .map_err(|e| format!("failed to serialize config: {e}"))?;
    fs::write(&path, json).map_err(|e| format!("failed to write {}: {e}", path.display()))
}

pub(crate) fn toml_string_list(value: Option<&toml::Value>) -> Vec<String> {
    let Some(value) = value else {
        return Vec::new();
    };
    if let Some(s) = value.as_str() {
        let trimmed = s.trim();
        if trimmed.is_empty() {
            return Vec::new();
        }
        return vec![trimmed.to_string()];
    }
    if let Some(arr) = value.as_array() {
        let mut out = Vec::new();
        for item in arr {
            if let Some(s) = item.as_str() {
                let trimmed = s.trim();
                if !trimmed.is_empty() {
                    out.push(trimmed.to_string());
                }
            }
        }
        return out;
    }
    Vec::new()
}

pub(crate) fn toml_u32(value: Option<&toml::Value>) -> Option<u32> {
    value
        .and_then(|v| v.as_integer())
        .and_then(|v| u32::try_from(v).ok())
}

pub(crate) fn toml_f64(value: Option<&toml::Value>) -> Option<f64> {
    value.and_then(|v| {
        if let Some(x) = v.as_float() {
            return Some(x);
        }
        v.as_integer().map(|x| x as f64)
    })
}

pub(crate) fn resolve_taxonomy_db_path(app: &AppHandle) -> Result<PathBuf, String> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join("taxonomy.db"));
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if let Some(tauri_gui_dir) = manifest_dir.parent() {
        candidates.push(tauri_gui_dir.join("resources").join("taxonomy.db"));
    }

    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("resources").join("taxonomy.db"));
        candidates.push(cwd.join("tauri-gui").join("resources").join("taxonomy.db"));
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            candidates.push(parent.join("taxonomy.db"));
            candidates.push(parent.join("resources").join("taxonomy.db"));
            if let Some(parent2) = parent.parent() {
                candidates.push(parent2.join("Resources").join("taxonomy.db"));
            }
        }
    }

    for path in &candidates {
        if path.exists() && path.is_file() {
            return Ok(path.clone());
        }
    }

    let searched = candidates
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join(", ");
    Err(format!(
        "taxonomy.db not found. Place it at tauri-gui/resources/taxonomy.db (searched: {searched})"
    ))
}

pub(crate) fn parse_db_toml_config(path: &Path) -> Result<ImportedDbTomlConfig, String> {
    let text =
        fs::read_to_string(path).map_err(|e| format!("failed to read {}: {e}", path.display()))?;
    let value: toml::Value = text
        .parse()
        .map_err(|e| format!("failed to parse {}: {e}", path.display()))?;

    let mut imported = ImportedDbTomlConfig {
        source_path: path.to_string_lossy().to_string(),
        ..ImportedDbTomlConfig::default()
    };
    let has_ncbi = value.get("ncbi").and_then(|v| v.as_table()).is_some();
    let has_bold = value.get("bold").and_then(|v| v.as_table()).is_some();
    imported.source = if has_ncbi && has_bold {
        "both".to_string()
    } else if !has_ncbi {
        "bold".to_string()
    } else {
        "ncbi".to_string()
    };

    if let Some(ncbi) = value.get("ncbi").and_then(|v| v.as_table()) {
        imported.email = ncbi
            .get("email")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        imported.api_key = ncbi
            .get("api_key")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        imported.ncbi_options.db = ncbi
            .get("db")
            .and_then(|v| v.as_str())
            .unwrap_or(DEFAULT_NCBI_DB)
            .trim()
            .to_string();
        imported.ncbi_options.rettype = ncbi
            .get("rettype")
            .and_then(|v| v.as_str())
            .unwrap_or(DEFAULT_NCBI_RETTYPE)
            .trim()
            .to_string();
        imported.ncbi_options.retmode = ncbi
            .get("retmode")
            .and_then(|v| v.as_str())
            .unwrap_or(DEFAULT_NCBI_RETMODE)
            .trim()
            .to_string();
        imported.ncbi_options.per_query =
            toml_u32(ncbi.get("per_query")).unwrap_or(DEFAULT_NCBI_PER_QUERY);
        imported.ncbi_options.use_history = ncbi
            .get("use_history")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        imported.ncbi_options.delay_sec = toml_f64(ncbi.get("delay_sec"));
    }

    if let Some(output) = value.get("output").and_then(|v| v.as_table()) {
        imported.output_options.default_header_format = output
            .get("default_header_format")
            .and_then(|v| v.as_str())
            .unwrap_or(DEFAULT_OUTPUT_HEADER_FORMAT)
            .trim()
            .to_string();
        if let Some(header_formats) = output.get("header_formats").and_then(|v| v.as_table()) {
            imported.output_options.mifish_header_format = header_formats
                .get("mifish_pipeline")
                .and_then(|v| v.as_str())
                .unwrap_or(DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT)
                .trim()
                .to_string();
        }
    }

    if let Some(filters) = value.get("filters").and_then(|v| v.as_table()) {
        let filter_terms = toml_string_list(filters.get("filter"));
        imported.filters.mitochondrion = filter_terms.iter().any(|v| v == "mitochondrion");
        imported.filters.ddbj_embl_genbank = filter_terms.iter().any(|v| v == "ddbj_embl_genbank");

        let properties = toml_string_list(filters.get("properties"));
        imported.filters.biomol_genomic = properties.iter().any(|v| v == "biomol_genomic");

        imported.filters.length_min = toml_u32(filters.get("sequence_length_min"));
        imported.filters.length_max = toml_u32(filters.get("sequence_length_max"));
    }

    if let Some(post) = value.get("post_prep").and_then(|v| v.as_table()) {
        imported.post_prep.enable = true;
        imported.post_prep.msa_tree_enable = post
            .get("msa_tree_enable")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        imported.post_prep.msa_tree_mode = post
            .get("msa_tree_mode")
            .and_then(|v| v.as_str())
            .unwrap_or(if imported.post_prep.msa_tree_enable {
                "combined"
            } else {
                DEFAULT_MSA_TREE_MODE
            })
            .trim()
            .to_string();
        imported.post_prep.primer_file = post
            .get("primer_file")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        imported.post_prep.primer_set = toml_string_list(post.get("primer_set"));
        imported.post_prep.sequence_length_min = toml_u32(post.get("sequence_length_min"));
        imported.post_prep.sequence_length_max = toml_u32(post.get("sequence_length_max"));
        imported.post_prep.primer_max_mismatch = toml_u32(post.get("primer_max_mismatch"));
        imported.post_prep.primer_max_error_rate =
            post.get("primer_max_error_rate").and_then(|v| v.as_float());
        imported.post_prep.primer_min_overlap_bp = toml_u32(post.get("primer_min_overlap_bp"));
        imported.post_prep.primer_min_overlap_ratio = post
            .get("primer_min_overlap_ratio")
            .and_then(|v| v.as_float());
        imported.post_prep.primer_end_max_offset = toml_u32(post.get("primer_end_max_offset"));
        imported.post_prep.primer_trim_mode = post
            .get("primer_trim_mode")
            .and_then(|v| v.as_str())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        imported.post_prep.primer_keep_retained_fasta = post
            .get("primer_keep_retained_fasta")
            .and_then(|v| v.as_bool());
        imported.post_prep.primer_iter_enable =
            post.get("primer_iter_enable").and_then(|v| v.as_bool());
        imported.post_prep.primer_iter_max_rounds = toml_u32(post.get("primer_iter_max_rounds"));
        imported.post_prep.primer_iter_stop_delta = post
            .get("primer_iter_stop_delta")
            .and_then(|v| v.as_float());
        imported.post_prep.primer_iter_target_conf = post
            .get("primer_iter_target_conf")
            .and_then(|v| v.as_float());
        imported.post_prep.primer_recheck_tool = post
            .get("primer_recheck_tool")
            .and_then(|v| v.as_str())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        imported.post_prep.primer_recheck_min_identity = post
            .get("primer_recheck_min_identity")
            .and_then(|v| v.as_float());
        imported.post_prep.primer_recheck_min_query_cov = post
            .get("primer_recheck_min_query_cov")
            .and_then(|v| v.as_float());
        imported.post_prep.primer_sidecar_format = post
            .get("primer_sidecar_format")
            .and_then(|v| v.as_str())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());

        let mut steps: Vec<String> = Vec::new();
        if !imported.post_prep.primer_file.is_empty() && !imported.post_prep.primer_set.is_empty() {
            steps.push("primer_trim".to_string());
        }
        if imported.post_prep.sequence_length_min.is_some()
            || imported.post_prep.sequence_length_max.is_some()
        {
            steps.push("length_filter".to_string());
        }
        steps.push("duplicate_report".to_string());
        imported.post_prep.steps = steps;
    }

    Ok(imported)
}

use chrono::Local;
use serde::{Deserialize, Deserializer, Serialize};
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
pub(crate) const DEFAULT_OUTPUT_PREFIX: &str = "MiFish";
pub(crate) const DEFAULT_OUTPUT_HEADER_FORMAT: &str =
    "{acc_id}|{organism}|{marker}|{label}|{type}|{loc}|{strand}";
pub(crate) const DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT: &str = "{db}|{acc_id}|{organism}";
pub(crate) const DEFAULT_MSA_TREE_MODE: &str = "disabled";

pub(crate) static MARKERS_TEMPLATE: &str = include_str!("../../../configs/markers_mitogenome.toml");
pub(crate) static PRIMERS_TEMPLATE: &str = include_str!("../../../configs/primers.toml");
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
    pub(crate) output_export_formats: Vec<String>,
}

impl Default for GuiConfig {
    fn default() -> Self {
        Self {
            source: DEFAULT_BUILD_SOURCE.to_string(),
            email: String::new(),
            api_key: String::new(),
            save_api_key: false,
            output_root: String::new(),
            output_prefix: DEFAULT_OUTPUT_PREFIX.to_string(),
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
            output_export_formats: Vec::new(),
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
    #[serde(default = "default_msa_tree_mode")]
    pub(crate) msa_tree_mode: String,
    pub(crate) primer_file: String,
    pub(crate) primer_set: Vec<String>,
    pub(crate) steps: Vec<String>,
    pub(crate) sequence_length_min: Option<u32>,
    pub(crate) sequence_length_max: Option<u32>,
    pub(crate) quality_max_ambiguous_fraction: Option<f64>,
    pub(crate) quality_reject_invalid_iupac: Option<bool>,
    pub(crate) duplicate_sequence_policy: Option<String>,
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
    pub(crate) export_formats: Vec<String>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(default)]
struct DbToml {
    #[serde(skip_serializing_if = "Option::is_none")]
    ncbi: Option<NcbiToml>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bold: Option<toml::Table>,
    output: OutputToml,
    taxon: TaxonToml,
    markers: MarkersToml,
    filters: FiltersToml,
    #[serde(skip_serializing_if = "Option::is_none")]
    post_prep: Option<PostPrepToml>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(default)]
struct NcbiToml {
    email: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    api_key: String,
    db: String,
    rettype: String,
    retmode: String,
    per_query: u32,
    use_history: bool,
    #[serde(
        default,
        deserialize_with = "deserialize_optional_f64",
        skip_serializing_if = "Option::is_none"
    )]
    delay_sec: Option<f64>,
}

impl Default for NcbiToml {
    fn default() -> Self {
        Self {
            email: String::new(),
            api_key: String::new(),
            db: DEFAULT_NCBI_DB.to_string(),
            rettype: DEFAULT_NCBI_RETTYPE.to_string(),
            retmode: DEFAULT_NCBI_RETMODE.to_string(),
            per_query: DEFAULT_NCBI_PER_QUERY,
            use_history: true,
            delay_sec: None,
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(default)]
struct OutputToml {
    default_header_format: String,
    header_formats: HeaderFormatsToml,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    export_formats: Vec<String>,
}

impl Default for OutputToml {
    fn default() -> Self {
        Self {
            default_header_format: DEFAULT_OUTPUT_HEADER_FORMAT.to_string(),
            header_formats: HeaderFormatsToml::default(),
            export_formats: Vec::new(),
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(default)]
struct HeaderFormatsToml {
    mifish_pipeline: String,
}

impl Default for HeaderFormatsToml {
    fn default() -> Self {
        Self {
            mifish_pipeline: DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT.to_string(),
        }
    }
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(default)]
struct TaxonToml {
    noexp: bool,
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(default)]
struct MarkersToml {
    file: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(default)]
struct FiltersToml {
    #[serde(
        default,
        deserialize_with = "deserialize_string_list",
        skip_serializing_if = "Vec::is_empty"
    )]
    filter: Vec<String>,
    #[serde(
        default,
        deserialize_with = "deserialize_string_list",
        skip_serializing_if = "Vec::is_empty"
    )]
    properties: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    sequence_length_min: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    sequence_length_max: Option<u32>,
}

fn deserialize_string_list<'de, D>(deserializer: D) -> Result<Vec<String>, D::Error>
where
    D: Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum OneOrMany {
        One(String),
        Many(Vec<String>),
    }

    Ok(match OneOrMany::deserialize(deserializer)? {
        OneOrMany::One(value) => vec![value],
        OneOrMany::Many(values) => values,
    })
}

fn deserialize_optional_f64<'de, D>(deserializer: D) -> Result<Option<f64>, D::Error>
where
    D: Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Number {
        Float(f64),
        Integer(i64),
    }

    Ok(
        Option::<Number>::deserialize(deserializer)?.map(|number| match number {
            Number::Float(value) => value,
            Number::Integer(value) => value as f64,
        }),
    )
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(default)]
struct PostPrepToml {
    msa_tree_enable: bool,
    msa_tree_mode: String,
    primer_file: String,
    #[serde(
        default,
        deserialize_with = "deserialize_string_list",
        skip_serializing_if = "Vec::is_empty"
    )]
    primer_set: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    sequence_length_min: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    sequence_length_max: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    quality_max_ambiguous_fraction: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    quality_reject_invalid_iupac: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    duplicate_sequence_policy: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_max_mismatch: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_max_error_rate: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_min_overlap_bp: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_min_overlap_ratio: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_end_max_offset: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_trim_mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_keep_retained_fasta: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_iter_enable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_iter_max_rounds: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_iter_stop_delta: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_iter_target_conf: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_recheck_tool: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_recheck_min_identity: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_recheck_min_query_cov: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    primer_sidecar_format: Option<String>,
}

impl Default for OutputOptionsInput {
    fn default() -> Self {
        Self {
            default_header_format: DEFAULT_OUTPUT_HEADER_FORMAT.to_string(),
            mifish_header_format: DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT.to_string(),
            export_formats: Vec::new(),
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
    #[serde(default)]
    pub(crate) base_config_path: String,
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
    pub(crate) marker_options: Vec<String>,
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

pub(crate) fn gui_config_path(app: &AppHandle) -> Result<PathBuf, String> {
    let home = app.path().home_dir().map_err(|e| e.to_string())?;
    Ok(home.join(CONFIG_DIR_NAME).join(CONFIG_FILE_NAME))
}

pub(crate) fn ensure_gui_config_parent(app: &AppHandle) -> Result<PathBuf, String> {
    let path = gui_config_path(app)?;
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

pub(crate) fn write_job_config(req: &RunRequest, config_dir: &Path) -> Result<PathBuf, String> {
    if req.output_options.export_formats.len() > 1 {
        return Err("only one output export format can be selected".to_string());
    }
    fs::create_dir_all(config_dir)
        .map_err(|e| format!("failed to create {}: {e}", config_dir.display()))?;

    fs::write(config_dir.join("markers_mitogenome.toml"), MARKERS_TEMPLATE)
        .map_err(|e| format!("failed to write markers template: {e}"))?;
    let bundled_primers_path = config_dir.join("primers.toml");
    fs::write(&bundled_primers_path, PRIMERS_TEMPLATE)
        .map_err(|e| format!("failed to write primers template: {e}"))?;

    let source = normalize_build_source(&req.source);
    let msa_tree_mode = match req.post_prep.msa_tree_mode.trim() {
        "combined" => "combined",
        "per_taxid" => "per_taxid",
        "disabled" => "disabled",
        _ => "disabled",
    };
    let config = DbToml {
        ncbi: source_uses_ncbi(&source).then(|| NcbiToml {
            email: req.email.trim().to_string(),
            api_key: req.api_key.trim().to_string(),
            db: nonempty_or(&req.ncbi_options.db, DEFAULT_NCBI_DB),
            rettype: nonempty_or(&req.ncbi_options.rettype, DEFAULT_NCBI_RETTYPE),
            retmode: nonempty_or(&req.ncbi_options.retmode, DEFAULT_NCBI_RETMODE),
            per_query: req.ncbi_options.per_query.max(1),
            use_history: req.ncbi_options.use_history,
            delay_sec: req.ncbi_options.delay_sec.filter(|value| *value > 0.0),
        }),
        output: OutputToml {
            default_header_format: nonempty_or(
                &req.output_options.default_header_format,
                DEFAULT_OUTPUT_HEADER_FORMAT,
            ),
            header_formats: HeaderFormatsToml {
                mifish_pipeline: nonempty_or(
                    &req.output_options.mifish_header_format,
                    DEFAULT_OUTPUT_MIFISH_HEADER_FORMAT,
                ),
            },
            export_formats: req.output_options.export_formats.clone(),
        },
        taxon: TaxonToml { noexp: false },
        markers: MarkersToml {
            file: "markers_mitogenome.toml".to_string(),
        },
        filters: FiltersToml {
            filter: [
                req.filters.mitochondrion.then_some("mitochondrion"),
                req.filters.ddbj_embl_genbank.then_some("ddbj_embl_genbank"),
            ]
            .into_iter()
            .flatten()
            .map(str::to_string)
            .collect(),
            properties: req
                .filters
                .biomol_genomic
                .then_some("biomol_genomic".to_string())
                .into_iter()
                .collect(),
            sequence_length_min: req.filters.length_min,
            sequence_length_max: req.filters.length_max,
        },
        post_prep: Some(PostPrepToml {
            msa_tree_enable: msa_tree_mode != "disabled",
            msa_tree_mode: msa_tree_mode.to_string(),
            primer_file: if req.post_prep.primer_file.trim().is_empty() {
                bundled_primers_path.to_string_lossy().to_string()
            } else {
                req.post_prep.primer_file.trim().to_string()
            },
            primer_set: req.post_prep.primer_set.clone(),
            sequence_length_min: req.post_prep.sequence_length_min,
            sequence_length_max: req.post_prep.sequence_length_max,
            quality_max_ambiguous_fraction: req.post_prep.quality_max_ambiguous_fraction,
            quality_reject_invalid_iupac: req.post_prep.quality_reject_invalid_iupac,
            duplicate_sequence_policy: trimmed_option(&req.post_prep.duplicate_sequence_policy),
            primer_max_mismatch: req.post_prep.primer_max_mismatch,
            primer_max_error_rate: req.post_prep.primer_max_error_rate,
            primer_min_overlap_bp: req.post_prep.primer_min_overlap_bp,
            primer_min_overlap_ratio: req.post_prep.primer_min_overlap_ratio,
            primer_end_max_offset: req.post_prep.primer_end_max_offset,
            primer_trim_mode: trimmed_option(&req.post_prep.primer_trim_mode),
            primer_keep_retained_fasta: req.post_prep.primer_keep_retained_fasta,
            primer_iter_enable: req.post_prep.primer_iter_enable,
            primer_iter_max_rounds: req.post_prep.primer_iter_max_rounds,
            primer_iter_stop_delta: req.post_prep.primer_iter_stop_delta,
            primer_iter_target_conf: req.post_prep.primer_iter_target_conf,
            primer_recheck_tool: trimmed_option(&req.post_prep.primer_recheck_tool),
            primer_recheck_min_identity: req.post_prep.primer_recheck_min_identity,
            primer_recheck_min_query_cov: req.post_prep.primer_recheck_min_query_cov,
            primer_sidecar_format: trimmed_option(&req.post_prep.primer_sidecar_format),
        }),
        ..DbToml::default()
    };
    let overlay_text = toml::to_string_pretty(&config)
        .map_err(|e| format!("failed to serialize job config: {e}"))?;
    let mut value: toml::Value =
        toml::from_str(&overlay_text).map_err(|e| format!("failed to prepare job config: {e}"))?;
    if !req.base_config_path.trim().is_empty() {
        let base_path = Path::new(req.base_config_path.trim());
        let base_text = fs::read_to_string(base_path)
            .map_err(|e| format!("failed to read {}: {e}", base_path.display()))?;
        let mut base: toml::Value = toml::from_str(&base_text)
            .map_err(|e| format!("failed to parse {}: {e}", base_path.display()))?;
        let base_dir = base_path.parent().unwrap_or_else(|| Path::new("."));
        absolutize_config_path(&mut base, "markers", "file", base_dir);
        absolutize_config_path(&mut base, "post_prep", "primer_file", base_dir);
        sync_owned_string_list(
            &mut base,
            "filters",
            "filter",
            &["mitochondrion", "ddbj_embl_genbank"],
            &config.filters.filter,
        );
        sync_owned_string_list(
            &mut base,
            "filters",
            "properties",
            &["biomol_genomic"],
            &config.filters.properties,
        );
        if let Some(table) = value.as_table_mut() {
            table.remove("markers");
            table.remove("taxon");
            if let Some(filters) = table.get_mut("filters").and_then(toml::Value::as_table_mut) {
                filters.remove("filter");
                filters.remove("properties");
            }
        }
        merge_toml(&mut base, value);
        value = base;
    }
    let text = toml::to_string_pretty(&value)
        .map_err(|e| format!("failed to serialize job config: {e}"))?;
    let config_path = config_dir.join("db.toml");
    fs::write(&config_path, text)
        .map_err(|e| format!("failed to write {}: {e}", config_path.display()))?;
    Ok(config_path)
}

fn nonempty_or(value: &str, default: &str) -> String {
    let value = value.trim();
    if value.is_empty() { default } else { value }.to_string()
}

fn trimmed_option(value: &Option<String>) -> Option<String> {
    value
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn absolutize_config_path(config: &mut toml::Value, section: &str, key: &str, base: &Path) {
    let Some(value) = config
        .get(section)
        .and_then(toml::Value::as_table)
        .and_then(|table| table.get(key))
        .and_then(toml::Value::as_str)
    else {
        return;
    };
    let path = Path::new(value);
    if path.is_relative() {
        config[section][key] = toml::Value::String(base.join(path).to_string_lossy().to_string());
    }
}

fn sync_owned_string_list(
    config: &mut toml::Value,
    section: &str,
    key: &str,
    owned: &[&str],
    selected: &[String],
) {
    let table = config
        .as_table_mut()
        .expect("TOML root must be a table")
        .entry(section.to_string())
        .or_insert_with(|| toml::Value::Table(toml::Table::new()))
        .as_table_mut()
        .expect("TOML section must be a table");
    let existing = match table.get(key) {
        Some(toml::Value::String(value)) => vec![value.as_str()],
        Some(toml::Value::Array(values)) => values.iter().filter_map(toml::Value::as_str).collect(),
        _ => Vec::new(),
    };
    let mut values = existing
        .into_iter()
        .filter(|value| !owned.contains(value))
        .map(|value| toml::Value::String(value.to_string()))
        .collect::<Vec<_>>();
    values.extend(selected.iter().cloned().map(toml::Value::String));
    table.insert(key.to_string(), toml::Value::Array(values));
}

fn merge_toml(base: &mut toml::Value, overlay: toml::Value) {
    match (base, overlay) {
        (toml::Value::Table(base), toml::Value::Table(overlay)) => {
            for (key, value) in overlay {
                if let Some(existing) = base.get_mut(&key) {
                    merge_toml(existing, value);
                } else {
                    base.insert(key, value);
                }
            }
        }
        (base, overlay) => *base = overlay,
    }
}
pub(crate) fn save_gui_config_internal(app: &AppHandle, config: &GuiConfig) -> Result<(), String> {
    let path = ensure_gui_config_parent(app)?;
    let mut saved = config.clone();
    if !saved.save_api_key {
        saved.api_key.clear();
    }
    let json = serde_json::to_string_pretty(&saved)
        .map_err(|e| format!("failed to serialize config: {e}"))?;
    fs::write(&path, json).map_err(|e| format!("failed to write {}: {e}", path.display()))
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
    let raw: toml::Value =
        toml::from_str(&text).map_err(|e| format!("failed to parse {}: {e}", path.display()))?;
    let marker_options = marker_options_from_config(&raw, path);
    let config: DbToml =
        toml::from_str(&text).map_err(|e| format!("failed to parse {}: {e}", path.display()))?;
    let has_ncbi = config.ncbi.is_some();
    let has_bold = config.bold.is_some();
    let mut imported = ImportedDbTomlConfig {
        source_path: path.to_string_lossy().to_string(),
        source: match (has_ncbi, has_bold) {
            (true, true) => "both",
            (true, false) => "ncbi",
            _ => "bold",
        }
        .to_string(),
        marker_options,
        ..ImportedDbTomlConfig::default()
    };

    if let Some(ncbi) = config.ncbi {
        imported.email = ncbi.email.trim().to_string();
        imported.api_key = ncbi.api_key.trim().to_string();
        imported.ncbi_options = NcbiOptionsInput {
            db: ncbi.db.trim().to_string(),
            rettype: ncbi.rettype.trim().to_string(),
            retmode: ncbi.retmode.trim().to_string(),
            per_query: ncbi.per_query,
            use_history: ncbi.use_history,
            delay_sec: ncbi.delay_sec,
        };
    }

    imported.output_options = OutputOptionsInput {
        default_header_format: config.output.default_header_format.trim().to_string(),
        mifish_header_format: config
            .output
            .header_formats
            .mifish_pipeline
            .trim()
            .to_string(),
        export_formats: config.output.export_formats,
    };
    imported.filters = FiltersInput {
        mitochondrion: config
            .filters
            .filter
            .iter()
            .any(|value| value.trim() == "mitochondrion"),
        ddbj_embl_genbank: config
            .filters
            .filter
            .iter()
            .any(|value| value.trim() == "ddbj_embl_genbank"),
        biomol_genomic: config
            .filters
            .properties
            .iter()
            .any(|value| value.trim() == "biomol_genomic"),
        length_min: config.filters.sequence_length_min,
        length_max: config.filters.sequence_length_max,
    };

    if let Some(post) = config.post_prep {
        let msa_tree_mode = if post.msa_tree_mode.trim().is_empty() {
            if post.msa_tree_enable {
                "combined"
            } else {
                DEFAULT_MSA_TREE_MODE
            }
            .to_string()
        } else {
            post.msa_tree_mode.trim().to_string()
        };
        let primer_file = post.primer_file.trim().to_string();
        let primer_set = post
            .primer_set
            .into_iter()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .collect::<Vec<_>>();
        let mut steps = Vec::new();
        if !primer_file.is_empty() && !primer_set.is_empty() {
            steps.push("primer_trim".to_string());
        }
        if post.sequence_length_min.is_some() || post.sequence_length_max.is_some() {
            steps.push("length_filter".to_string());
        }
        if post.quality_max_ambiguous_fraction.is_some()
            || post.quality_reject_invalid_iupac.is_some()
            || post.duplicate_sequence_policy.is_some()
        {
            steps.push("quality_filter".to_string());
        }
        steps.push("duplicate_report".to_string());

        imported.post_prep = PostPrepInput {
            enable: true,
            msa_tree_mode,
            primer_file,
            primer_set,
            steps,
            sequence_length_min: post.sequence_length_min,
            sequence_length_max: post.sequence_length_max,
            quality_max_ambiguous_fraction: post.quality_max_ambiguous_fraction,
            quality_reject_invalid_iupac: post.quality_reject_invalid_iupac,
            duplicate_sequence_policy: post.duplicate_sequence_policy,
            primer_max_mismatch: post.primer_max_mismatch,
            primer_max_error_rate: post.primer_max_error_rate,
            primer_min_overlap_bp: post.primer_min_overlap_bp,
            primer_min_overlap_ratio: post.primer_min_overlap_ratio,
            primer_end_max_offset: post.primer_end_max_offset,
            primer_trim_mode: trimmed_option(&post.primer_trim_mode),
            primer_keep_retained_fasta: post.primer_keep_retained_fasta,
            primer_iter_enable: post.primer_iter_enable,
            primer_iter_max_rounds: post.primer_iter_max_rounds,
            primer_iter_stop_delta: post.primer_iter_stop_delta,
            primer_iter_target_conf: post.primer_iter_target_conf,
            primer_recheck_tool: trimmed_option(&post.primer_recheck_tool),
            primer_recheck_min_identity: post.primer_recheck_min_identity,
            primer_recheck_min_query_cov: post.primer_recheck_min_query_cov,
            primer_sidecar_format: trimmed_option(&post.primer_sidecar_format),
        };
    }

    Ok(imported)
}

fn marker_options_from_config(config: &toml::Value, config_path: &Path) -> Vec<String> {
    let Some(markers) = config.get("markers").and_then(toml::Value::as_table) else {
        return Vec::new();
    };
    let mut options = markers
        .keys()
        .filter(|key| key.as_str() != "file")
        .cloned()
        .collect::<Vec<_>>();
    if let Some(file) = markers.get("file").and_then(toml::Value::as_str) {
        let path = Path::new(file);
        let path = if path.is_relative() {
            config_path
                .parent()
                .unwrap_or_else(|| Path::new("."))
                .join(path)
        } else {
            path.to_path_buf()
        };
        if let Ok(text) = fs::read_to_string(path) {
            if let Ok(external) = toml::from_str::<toml::Value>(&text) {
                if let Some(markers) = external.get("markers").and_then(toml::Value::as_table) {
                    options.extend(markers.keys().filter(|key| key.as_str() != "file").cloned());
                }
            }
        }
    }
    options.sort();
    options.dedup();
    options
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn parses_existing_db_toml_without_field_copying() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join("tests/fixtures/post_prep_config.toml");
        let config = parse_db_toml_config(&path).expect("parse fixture");

        assert_eq!(config.source, "ncbi");
        assert!(config.post_prep.steps.contains(&"primer_trim".to_string()));
        assert!(config
            .post_prep
            .steps
            .contains(&"length_filter".to_string()));
    }

    #[test]
    fn writes_job_config_with_serde() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("taxondb-config-{stamp}"));
        let mut request = RunRequest {
            taxids: vec!["999".to_string()],
            markers: vec!["12s".to_string()],
            source: "ncbi".to_string(),
            output_prefix: String::new(),
            output_root: dir.to_string_lossy().to_string(),
            email: "test@example.com".to_string(),
            api_key: String::new(),
            save_api_key: false,
            base_config_path: String::new(),
            filters: FiltersInput::default(),
            post_prep: PostPrepInput {
                msa_tree_mode: "combined".to_string(),
                ..PostPrepInput::default()
            },
            workers: 1,
            resume: false,
            ncbi_options: NcbiOptionsInput::default(),
            output_options: OutputOptionsInput {
                export_formats: vec!["qiime2".to_string()],
                ..OutputOptionsInput::default()
            },
        };

        let path = write_job_config(&request, &dir).expect("write config");
        let config: DbToml = toml::from_str(&fs::read_to_string(path).expect("read config"))
            .expect("parse generated config");
        assert_eq!(config.ncbi.expect("ncbi").email, "test@example.com");
        let post_prep = config.post_prep.expect("post_prep");
        assert!(post_prep.msa_tree_enable);
        assert_eq!(post_prep.msa_tree_mode, "combined");
        assert_eq!(config.output.export_formats, vec!["qiime2"]);
        assert!(fs::read_to_string(dir.join("primers.toml"))
            .expect("primers template")
            .contains("primer_sets.libird"));
        request.output_options.export_formats =
            vec!["qiime2".to_string(), "dada2_species".to_string()];
        assert!(write_job_config(&request, &dir)
            .expect_err("reject multiple export formats")
            .contains("only one output export format"));

        request.output_options.export_formats = vec!["qiime2".to_string()];
        let base_path = dir.join("imported.toml");
        fs::write(
            dir.join("custom_markers.toml"),
            "[markers.custom]\nphrases = [\"custom\"]\n",
        )
        .expect("write marker config");
        fs::write(
            &base_path,
            "[markers]\nfile = \"custom_markers.toml\"\n\n[filters]\nfilter = [\"mitochondrion\", \"custom_filter\"]\nproperties = \"custom_property\"\nadvanced = true\n",
        )
        .expect("write base config");
        request.base_config_path = base_path.to_string_lossy().to_string();
        let imported = parse_db_toml_config(&base_path).expect("import base config");
        assert_eq!(imported.marker_options, vec!["custom"]);
        let merged_path = write_job_config(&request, &dir.join("merged")).expect("merge config");
        let merged: toml::Value =
            toml::from_str(&fs::read_to_string(merged_path).expect("read merged config"))
                .expect("parse merged config");
        assert_eq!(
            merged["filters"]["filter"]
                .as_array()
                .expect("filter")
                .len(),
            1
        );
        assert_eq!(
            merged["filters"]["filter"][0].as_str(),
            Some("custom_filter")
        );
        assert_eq!(merged["filters"]["advanced"].as_bool(), Some(true));
        assert!(Path::new(merged["markers"]["file"].as_str().expect("marker file")).is_absolute());
        fs::remove_dir_all(dir).expect("remove test directory");
    }
}

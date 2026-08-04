fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let target = std::env::var("TARGET").expect("TARGET is set by Cargo");
    let sidecar = manifest_dir
        .join("binaries")
        .join(format!("taxondbbuilder-{target}"));

    // tauri-build validates externalBin during every cargo build. Keep the
    // checked-in declaration for packaging, but allow development builds to
    // compile before a target-specific PyInstaller artifact is available.
    if !sidecar.is_file() {
        // TAURI_CONFIG is a JSON merge patch consumed by tauri-build.
        std::env::set_var("TAURI_CONFIG", r#"{"bundle":{"externalBin":[]}}"#);
    }

    tauri_build::build()
}

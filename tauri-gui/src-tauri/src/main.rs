#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod config;
mod progress;
mod sidecar;
mod state;

use commands::{
    cancel_run, choose_db_toml_file, choose_output_directory, choose_primer_file, import_db_toml,
    load_gui_config, open_path, read_text_file, save_gui_config, save_svg_file, search_taxonomy,
    start_run,
};
use state::AppState;

fn main() {
    tauri::Builder::default()
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            load_gui_config,
            save_gui_config,
            choose_output_directory,
            choose_primer_file,
            choose_db_toml_file,
            search_taxonomy,
            import_db_toml,
            open_path,
            read_text_file,
            save_svg_file,
            start_run,
            cancel_run
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

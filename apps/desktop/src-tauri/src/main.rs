#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::path::PathBuf;
use tauri::Manager;

struct BackendChildren(Mutex<Vec<Child>>);

fn start_backend(command: &str) -> Option<Child> {
    let executable = std::env::var_os("DECKFORGE_CLI").map(PathBuf::from).unwrap_or_else(|| {
        std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/bin/deckforge")).filter(|path| path.exists()).unwrap_or_else(|| PathBuf::from("deckforge"))
    });
    Command::new(executable)
        .arg(command)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let mut children = Vec::new();
            if let Some(child) = start_backend("serve") { children.push(child); }
            if let Some(child) = start_backend("daemon") { children.push(child); }
            app.manage(BackendChildren(Mutex::new(children)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build DeckForge");

    // Dropping Child handles does not stop their processes on Unix. This lets
    // the event daemon continue after the editor window closes.
    app.run(|_, _| {});
}

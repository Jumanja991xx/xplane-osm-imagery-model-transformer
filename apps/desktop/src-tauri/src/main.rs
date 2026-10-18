// Release builds are GUI-subsystem executables: no console window on Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use std::sync::Arc;

use sidecar::{Backend, BackendInfo};
use tauri::{Manager, RunEvent, State};

/// Called by the frontend once; blocks until the backend announced its port.
#[tauri::command]
async fn get_backend_info(backend: State<'_, Arc<Backend>>) -> Result<BackendInfo, String> {
    let backend = Arc::clone(&backend);
    tauri::async_runtime::spawn_blocking(move || backend.wait_ready())
        .await
        .map_err(|e| e.to_string())?
}

fn main() {
    let backend = Arc::new(Backend::default());

    let app = tauri::Builder::default()
        .manage(Arc::clone(&backend))
        .invoke_handler(tauri::generate_handler![get_backend_info])
        .setup(move |app| {
            let resource_dir = app.path().resource_dir().ok().map(|p| dunce::simplified(&p).to_path_buf());
            backend.start(resource_dir);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the application");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<Arc<Backend>>().stop();
        }
    });
}

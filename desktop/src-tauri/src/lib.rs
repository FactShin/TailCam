pub mod node;
pub mod notifications;
pub mod tray;
pub mod windows;

use std::sync::Mutex;

use tauri::{Manager, WindowEvent};
use tauri_plugin_autostart::ManagerExt;

#[derive(Default)]
pub struct DesktopState {
    node: Mutex<Option<node::NodeProcess>>,
    origin: Mutex<Option<String>>,
}

#[tauri::command]
fn open_main_window(app: tauri::AppHandle) -> Result<(), String> {
    windows::open_main_window(&app)
}

#[tauri::command]
fn open_main_route(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
    path: String,
) -> Result<(), String> {
    let origin = state.origin.lock().map_err(|err| err.to_string())?.clone();
    windows::open_main_route(&app, origin.as_deref(), &path)
}

#[tauri::command]
fn quit_tailcam(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<(), String> {
    if let Some(mut node) = state.node.lock().map_err(|err| err.to_string())?.take() {
        node.stop_owned();
    }
    app.exit(0);
    Ok(())
}

#[tauri::command]
fn get_launch_at_login(app: tauri::AppHandle) -> Result<bool, String> {
    app.autolaunch().is_enabled().map_err(|err| err.to_string())
}

#[tauri::command]
fn set_launch_at_login(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    let autostart = app.autolaunch();
    if enabled {
        autostart.enable()
    } else {
        autostart.disable()
    }
    .map_err(|err| err.to_string())
}

fn io_error(message: String) -> std::io::Error {
    std::io::Error::other(message)
}

fn navigation_guard<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    tauri::plugin::Builder::new("tailcam-navigation")
        .on_navigation(|webview, url| {
            let label = webview.label();
            if label != windows::MAIN_WINDOW && label != windows::COMMAND_CENTER_WINDOW {
                return true;
            }
            let origin = webview
                .state::<DesktopState>()
                .origin
                .lock()
                .ok()
                .and_then(|origin| origin.clone());
            windows::navigation_allowed(label, origin.as_deref(), url.as_str())
        })
        .build()
}

pub fn run() {
    tauri::Builder::default()
        .plugin(navigation_guard())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(DesktopState::default())
        .setup(|app| {
            let node = node::start_or_attach().map_err(io_error)?;
            let origin = node.origin().to_string();
            let state = app.state::<DesktopState>();
            *state
                .origin
                .lock()
                .map_err(|err| io_error(err.to_string()))? = Some(origin.clone());
            *state.node.lock().map_err(|err| io_error(err.to_string()))? = Some(node);
            windows::navigate_app_windows(app.handle(), &origin).map_err(io_error)?;
            tray::install(app.handle()).map_err(io_error)?;
            notifications::start(app.handle().clone(), origin);
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == windows::MAIN_WINDOW {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            open_main_window,
            open_main_route,
            quit_tailcam,
            get_launch_at_login,
            set_launch_at_login
        ])
        .run(tauri::generate_context!())
        .expect("error while running TailCam desktop app");
}

use tauri::{AppHandle, Manager, Rect, Runtime};

pub const MAIN_WINDOW: &str = "main";
pub const COMMAND_CENTER_WINDOW: &str = "command-center";

pub fn open_main_window<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let window = app
        .get_webview_window(MAIN_WINDOW)
        .ok_or_else(|| "TailCam main window is not available".to_string())?;
    window.show().map_err(|err| err.to_string())?;
    window.set_focus().map_err(|err| err.to_string())
}

pub fn open_main_route<R: Runtime>(
    app: &AppHandle<R>,
    origin: Option<&str>,
    path: &str,
) -> Result<(), String> {
    let origin = origin.ok_or_else(|| "TailCam local origin is not available".to_string())?;
    let url = main_route_url(origin, path)?;
    let window = app
        .get_webview_window(MAIN_WINDOW)
        .ok_or_else(|| "TailCam main window is not available".to_string())?;
    window.navigate(url).map_err(|err| err.to_string())?;
    window.show().map_err(|err| err.to_string())?;
    window.set_focus().map_err(|err| err.to_string())
}

pub fn toggle_command_center<R: Runtime>(
    app: &AppHandle<R>,
    tray_rect: Option<Rect>,
) -> Result<(), String> {
    let window = app
        .get_webview_window(COMMAND_CENTER_WINDOW)
        .ok_or_else(|| "TailCam command center window is not available".to_string())?;
    if window.is_visible().map_err(|err| err.to_string())? {
        return window.hide().map_err(|err| err.to_string());
    }
    if let Some(rect) = tray_rect {
        let _ = window.set_position(rect.position);
    }
    window.show().map_err(|err| err.to_string())?;
    window.set_focus().map_err(|err| err.to_string())
}

pub fn navigate_app_windows<R: Runtime>(app: &AppHandle<R>, origin: &str) -> Result<(), String> {
    if let Some(main) = app.get_webview_window(MAIN_WINDOW) {
        let url = tauri::Url::parse(&format!("{origin}/")).map_err(|err| err.to_string())?;
        main.navigate(url).map_err(|err| err.to_string())?;
    }
    if let Some(command_center) = app.get_webview_window(COMMAND_CENTER_WINDOW) {
        let url = tauri::Url::parse(&format!("{origin}/desktop/command-center"))
            .map_err(|err| err.to_string())?;
        command_center
            .navigate(url)
            .map_err(|err| err.to_string())?;
    }
    Ok(())
}

fn main_route_url(origin: &str, path: &str) -> Result<tauri::Url, String> {
    if !path.starts_with('/') || path.starts_with("//") {
        return Err("TailCam route must be an absolute app path".to_string());
    }
    tauri::Url::parse(&format!("{origin}{path}")).map_err(|err| err.to_string())
}

pub fn navigation_allowed(label: &str, allowed_origin: Option<&str>, url: &str) -> bool {
    if label != MAIN_WINDOW && label != COMMAND_CENTER_WINDOW {
        return true;
    }
    let Some(origin) = allowed_origin else {
        return false;
    };
    url == origin || url.starts_with(&format!("{origin}/"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_is_limited_to_validated_tailcam_origin() {
        assert!(navigation_allowed(
            MAIN_WINDOW,
            Some("http://127.0.0.1:8088"),
            "http://127.0.0.1:8088/fleet"
        ));
        assert!(!navigation_allowed(
            MAIN_WINDOW,
            Some("http://127.0.0.1:8088"),
            "http://127.0.0.1:9999/"
        ));
        assert!(navigation_allowed(
            "other",
            Some("http://127.0.0.1:8088"),
            "https://example.com/"
        ));
    }

    #[test]
    fn main_route_urls_are_limited_to_tailcam_app_paths() {
        assert_eq!(
            main_route_url("http://127.0.0.1:8088", "/events")
                .unwrap()
                .as_str(),
            "http://127.0.0.1:8088/events"
        );
        assert!(main_route_url("http://127.0.0.1:8088", "events").is_err());
        assert!(main_route_url("http://127.0.0.1:8088", "//evil.example").is_err());
        assert!(main_route_url("http://127.0.0.1:8088", "https://evil.example/events").is_err());
    }
}

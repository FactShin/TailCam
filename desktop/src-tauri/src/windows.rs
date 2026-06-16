use tauri::{AppHandle, Manager};

pub const MAIN_WINDOW: &str = "main";
pub const COMMAND_CENTER_WINDOW: &str = "command-center";

pub fn open_main_window(app: &AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window(MAIN_WINDOW)
        .ok_or_else(|| "TailCam main window is not available".to_string())?;
    window.show().map_err(|err| err.to_string())?;
    window.set_focus().map_err(|err| err.to_string())
}

pub fn navigate_app_windows(app: &AppHandle, origin: &str) -> Result<(), String> {
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
}

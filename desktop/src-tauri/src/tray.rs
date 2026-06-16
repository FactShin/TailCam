use tauri::{
    image::Image,
    menu::MenuBuilder,
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Rect, Runtime,
};

use crate::windows;

pub const TRAY_ID: &str = "tailcam-menu-bar";
pub const MENU_OPEN_TAILCAM: &str = "open-tailcam";
pub const MENU_QUIT_TAILCAM: &str = "quit-tailcam";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayAction {
    ToggleCommandCenter,
    OpenMainWindow,
    QuitTailCam,
}

pub fn menu_action(id: &str) -> Option<TrayAction> {
    match id {
        MENU_OPEN_TAILCAM => Some(TrayAction::OpenMainWindow),
        MENU_QUIT_TAILCAM => Some(TrayAction::QuitTailCam),
        _ => None,
    }
}

pub fn stable_window_labels() -> [&'static str; 2] {
    [windows::MAIN_WINDOW, windows::COMMAND_CENTER_WINDOW]
}

pub fn is_primary_click(event: &TrayIconEvent) -> bool {
    matches!(
        event,
        TrayIconEvent::Click {
            button: MouseButton::Left,
            button_state: MouseButtonState::Up,
            ..
        }
    )
}

pub fn install<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let menu = MenuBuilder::new(app)
        .text(MENU_OPEN_TAILCAM, "Open TailCam")
        .separator()
        .text(MENU_QUIT_TAILCAM, "Quit TailCam")
        .build()
        .map_err(|err| err.to_string())?;

    let icon =
        Image::from_bytes(include_bytes!("../icons/icon.png")).map_err(|err| err.to_string())?;
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon)
        .icon_as_template(cfg!(target_os = "macos"))
        .tooltip("TailCam")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            if let Some(action) = menu_action(event.id().as_ref()) {
                handle_action(app, action, None);
            }
        })
        .on_tray_icon_event(|tray, event| {
            if is_primary_click(&event) {
                handle_action(
                    tray.app_handle(),
                    TrayAction::ToggleCommandCenter,
                    tray_rect(&event),
                );
            }
        })
        .build(app)
        .map_err(|err| err.to_string())?;
    Ok(())
}

fn tray_rect(event: &TrayIconEvent) -> Option<Rect> {
    match event {
        TrayIconEvent::Click { rect, .. }
        | TrayIconEvent::DoubleClick { rect, .. }
        | TrayIconEvent::Enter { rect, .. }
        | TrayIconEvent::Move { rect, .. }
        | TrayIconEvent::Leave { rect, .. } => Some(*rect),
        _ => None,
    }
}

fn handle_action<R: Runtime>(app: &AppHandle<R>, action: TrayAction, tray_rect: Option<Rect>) {
    match action {
        TrayAction::ToggleCommandCenter => {
            let _ = windows::toggle_command_center(app, tray_rect);
        }
        TrayAction::OpenMainWindow => {
            let _ = windows::open_main_window(app);
        }
        TrayAction::QuitTailCam => {
            app.exit(0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_menu_ids_map_to_stable_actions() {
        assert_eq!(
            menu_action(MENU_OPEN_TAILCAM),
            Some(TrayAction::OpenMainWindow)
        );
        assert_eq!(
            menu_action(MENU_QUIT_TAILCAM),
            Some(TrayAction::QuitTailCam)
        );
        assert_eq!(menu_action("unknown"), None);
    }

    #[test]
    fn tray_uses_stable_window_labels() {
        assert_eq!(stable_window_labels(), ["main", "command-center"]);
    }
}

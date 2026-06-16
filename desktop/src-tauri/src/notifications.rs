use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream};
use std::time::Duration;

use serde::Deserialize;
use tauri::{AppHandle, Runtime};
use tauri_plugin_notification::NotificationExt;

const POLL_INTERVAL: Duration = Duration::from_secs(5);

#[derive(Debug, Default)]
pub struct EventDeduper {
    primed: bool,
    last_seen_id: Option<i64>,
}

impl EventDeduper {
    pub fn observe_latest(&mut self, latest_id: Option<i64>) -> Option<i64> {
        if !self.primed {
            self.primed = true;
            self.last_seen_id = latest_id;
            return None;
        }
        if latest_id.is_some() && latest_id != self.last_seen_id {
            self.last_seen_id = latest_id;
            return latest_id;
        }
        None
    }
}

#[derive(Debug, Deserialize)]
struct MotionEvent {
    id: i64,
    camera_id: String,
    label: Option<String>,
    description: Option<String>,
}

#[derive(Debug, Deserialize)]
struct NodeHealth {
    host: String,
    version: String,
}

pub fn start<R: Runtime>(app: AppHandle<R>, origin: String) {
    std::thread::spawn(move || {
        let mut deduper = EventDeduper::default();
        loop {
            if local_node_healthy(&origin) {
                if let Some(event) = latest_local_event(&origin) {
                    if deduper.observe_latest(Some(event.id)).is_some() {
                        let _ = show_event_notification(&app, &event);
                    }
                } else {
                    let _ = deduper.observe_latest(None);
                }
            }
            std::thread::sleep(POLL_INTERVAL);
        }
    });
}

fn show_event_notification<R: Runtime>(
    app: &AppHandle<R>,
    event: &MotionEvent,
) -> Result<(), String> {
    let _ = app.notification().request_permission();
    app.notification()
        .builder()
        .title(notification_title(event))
        .body(notification_body(event))
        .group("tailcam-events")
        .extra("route", "/events")
        .extra("eventId", event.id)
        .auto_cancel()
        .show()
        .map_err(|err| err.to_string())
}

fn notification_title(event: &MotionEvent) -> String {
    match event.label.as_deref().filter(|label| !label.is_empty()) {
        Some(label) => format!("TailCam detected {label}"),
        None => "TailCam motion detected".to_string(),
    }
}

fn notification_body(event: &MotionEvent) -> String {
    event
        .description
        .as_deref()
        .filter(|description| !description.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Camera {}", event.camera_id))
}

fn local_node_healthy(origin: &str) -> bool {
    let Some(body) = http_get(origin, "/api/v1/node/health") else {
        return false;
    };
    let Ok(health) = serde_json::from_str::<NodeHealth>(&body) else {
        return false;
    };
    !health.host.is_empty() && !health.version.is_empty()
}

fn latest_local_event(origin: &str) -> Option<MotionEvent> {
    let body = http_get(origin, "/api/events?scope=local&limit=1")?;
    serde_json::from_str::<Vec<MotionEvent>>(&body)
        .ok()?
        .into_iter()
        .next()
}

fn http_get(origin: &str, path: &str) -> Option<String> {
    let host_port = origin.strip_prefix("http://")?;
    let mut stream = TcpStream::connect(host_port).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .ok()?;
    let request = format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let _ = stream.shutdown(Shutdown::Write);
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.starts_with("HTTP/1.1 200") && !response.starts_with("HTTP/1.0 200") {
        return None;
    }
    let (_, body) = response.split_once("\r\n\r\n")?;
    Some(body.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_deduper_primes_then_reports_new_latest_ids_once() {
        let mut deduper = EventDeduper::default();

        assert_eq!(deduper.observe_latest(Some(7)), None);
        assert_eq!(deduper.observe_latest(Some(7)), None);
        assert_eq!(deduper.observe_latest(Some(8)), Some(8));
        assert_eq!(deduper.observe_latest(Some(8)), None);
        assert_eq!(deduper.observe_latest(None), None);
        assert_eq!(deduper.observe_latest(Some(9)), Some(9));
    }

    #[test]
    fn notification_copy_prefers_ai_label_and_description() {
        let event = MotionEvent {
            id: 10,
            camera_id: "front-door".to_string(),
            label: Some("person".to_string()),
            description: Some("Person at the front door".to_string()),
        };

        assert_eq!(notification_title(&event), "TailCam detected person");
        assert_eq!(notification_body(&event), "Person at the front door");
    }
}

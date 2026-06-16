use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde::Deserialize;

const DEFAULT_PORT: u16 = 8088;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProbeResult {
    TailCam { origin: String },
    EmptyPort,
    Unavailable,
    OccupiedByOther,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeDisposition {
    Attach,
    Spawn,
    PortConflict,
}

pub fn disposition(probe: ProbeResult) -> NodeDisposition {
    match probe {
        ProbeResult::TailCam { .. } => NodeDisposition::Attach,
        ProbeResult::EmptyPort | ProbeResult::Unavailable => NodeDisposition::Spawn,
        ProbeResult::OccupiedByOther => NodeDisposition::PortConflict,
    }
}

#[derive(Debug)]
pub struct NodeProcess {
    origin: String,
    owned: bool,
    child: Option<Child>,
}

impl NodeProcess {
    pub fn attached(origin: String) -> Self {
        Self {
            origin,
            owned: false,
            child: None,
        }
    }

    pub fn owned(origin: String, child: Child) -> Self {
        Self {
            origin,
            owned: true,
            child: Some(child),
        }
    }

    #[cfg(test)]
    pub fn owned_for_test(origin: String) -> Self {
        Self {
            origin,
            owned: true,
            child: None,
        }
    }

    pub fn origin(&self) -> &str {
        &self.origin
    }

    pub fn stop_owned(&mut self) -> bool {
        if !self.owned {
            return false;
        }
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.child = None;
        self.owned = false;
        true
    }
}

impl Drop for NodeProcess {
    fn drop(&mut self) {
        let _ = self.stop_owned();
    }
}

#[derive(Debug, Deserialize)]
struct SystemInfo {
    version: Option<String>,
    host: Option<String>,
}

pub fn probe_origin(origin: &str) -> ProbeResult {
    match get_system(origin) {
        Ok(Some(_)) => ProbeResult::TailCam {
            origin: origin.to_string(),
        },
        Ok(None) => ProbeResult::OccupiedByOther,
        Err(ProbeError::ConnectionRefused) => ProbeResult::EmptyPort,
        Err(ProbeError::Unavailable) => ProbeResult::Unavailable,
    }
}

pub fn start_or_attach() -> Result<NodeProcess, String> {
    let port = configured_port();
    let origin = origin_for_port(port);
    match disposition(probe_origin(&origin)) {
        NodeDisposition::Attach => Ok(NodeProcess::attached(origin)),
        NodeDisposition::PortConflict => {
            Err(format!("Port {port} is in use by a non-TailCam service"))
        }
        NodeDisposition::Spawn => {
            let child = spawn_sidecar()?;
            wait_for_tailcam(&origin, Duration::from_secs(30))?;
            Ok(NodeProcess::owned(origin, child))
        }
    }
}

fn wait_for_tailcam(origin: &str, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if matches!(probe_origin(origin), ProbeResult::TailCam { .. }) {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err(format!("Timed out waiting for TailCam node at {origin}"))
}

fn spawn_sidecar() -> Result<Child, String> {
    let sidecar = sidecar_path()?;
    Command::new(sidecar)
        .arg("run")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("Failed to spawn TailCam node sidecar: {err}"))
}

fn sidecar_path() -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var("TAILCAM_NODE_SIDECAR") {
        return Ok(PathBuf::from(path));
    }
    let exe = std::env::current_exe().map_err(|err| err.to_string())?;
    let dir = exe
        .parent()
        .ok_or_else(|| "Could not locate TailCam desktop executable directory".to_string())?;
    Ok(dir.join(format!("tailcam-node-{}", current_target_triple())))
}

fn current_target_triple() -> &'static str {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "aarch64-apple-darwin"
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        "x86_64-apple-darwin"
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "x86_64-pc-windows-msvc.exe"
    } else if cfg!(all(target_os = "windows", target_arch = "aarch64")) {
        "aarch64-pc-windows-msvc.exe"
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        "x86_64-unknown-linux-gnu"
    } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
        "aarch64-unknown-linux-gnu"
    } else {
        "unknown"
    }
}

fn configured_port() -> u16 {
    let env_port = std::env::var("TAILCAM_PORT").ok();
    let config = std::fs::read_to_string(config_file_path()).ok();
    choose_configured_port(env_port.as_deref(), config.as_deref())
}

fn choose_configured_port(env_port: Option<&str>, config: Option<&str>) -> u16 {
    env_port
        .and_then(parse_port)
        .or_else(|| config.and_then(port_from_config))
        .unwrap_or(DEFAULT_PORT)
}

fn parse_port(raw: &str) -> Option<u16> {
    raw.parse::<u16>().ok().filter(|port| *port > 0)
}

fn port_from_config(raw: &str) -> Option<u16> {
    let value: toml::Table = toml::from_str(raw).ok()?;
    let port = value.get("server")?.get("port")?.as_integer()?;
    u16::try_from(port).ok().filter(|port| *port > 0)
}

fn origin_for_port(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

fn config_file_path() -> PathBuf {
    if let Ok(path) = std::env::var("TAILCAM_CONFIG") {
        return expand_home(path);
    }
    config_dir().join("config.toml")
}

fn config_dir() -> PathBuf {
    if let Ok(path) = std::env::var("TAILCAM_CONFIG_DIR") {
        return expand_home(path);
    }
    config_base().join(app_dir_name())
}

fn config_base() -> PathBuf {
    if cfg!(target_os = "windows") {
        std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join("AppData").join("Roaming"))
    } else if cfg!(target_os = "macos") {
        home_dir().join("Library").join("Application Support")
    } else {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".config"))
    }
}

fn app_dir_name() -> &'static str {
    if cfg!(any(target_os = "macos", target_os = "windows")) {
        "TailCam"
    } else {
        "tailcam"
    }
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn expand_home(path: String) -> PathBuf {
    if path == "~" {
        return home_dir();
    }
    if let Some(rest) = path.strip_prefix("~/") {
        return home_dir().join(rest);
    }
    PathBuf::from(path)
}

#[derive(Debug)]
enum ProbeError {
    ConnectionRefused,
    Unavailable,
}

fn get_system(origin: &str) -> Result<Option<SystemInfo>, ProbeError> {
    let Some(host_port) = origin.strip_prefix("http://") else {
        return Err(ProbeError::Unavailable);
    };
    let mut stream = TcpStream::connect(host_port).map_err(|err| {
        if err.kind() == std::io::ErrorKind::ConnectionRefused {
            ProbeError::ConnectionRefused
        } else {
            ProbeError::Unavailable
        }
    })?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| ProbeError::Unavailable)?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| ProbeError::Unavailable)?;
    stream
        .write_all(b"GET /api/system HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .map_err(|_| ProbeError::Unavailable)?;
    let _ = stream.shutdown(Shutdown::Write);
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|_| ProbeError::Unavailable)?;
    if !response.starts_with("HTTP/1.1 200") && !response.starts_with("HTTP/1.0 200") {
        return Ok(None);
    }
    let Some((_, body)) = response.split_once("\r\n\r\n") else {
        return Ok(None);
    };
    let system: SystemInfo = serde_json::from_str(body).map_err(|_| ProbeError::Unavailable)?;
    if system.version.as_deref().unwrap_or("").is_empty()
        || system.host.as_deref().unwrap_or("").is_empty()
    {
        return Ok(None);
    }
    Ok(Some(system))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disposition_attaches_spawns_or_reports_port_conflict() {
        assert_eq!(
            disposition(ProbeResult::TailCam {
                origin: "http://127.0.0.1:8088".into(),
            }),
            NodeDisposition::Attach
        );
        assert_eq!(
            disposition(ProbeResult::Unavailable),
            NodeDisposition::Spawn
        );
        assert_eq!(disposition(ProbeResult::EmptyPort), NodeDisposition::Spawn);
        assert_eq!(
            disposition(ProbeResult::OccupiedByOther),
            NodeDisposition::PortConflict
        );
    }

    #[test]
    fn stop_owned_process_only_stops_owned_sidecars() {
        let mut attached = NodeProcess::attached("http://127.0.0.1:8088".into());
        assert!(!attached.stop_owned());

        let mut owned = NodeProcess::owned_for_test("http://127.0.0.1:8088".into());
        assert!(owned.stop_owned());
        assert!(!owned.stop_owned());
    }

    #[test]
    fn configured_port_prefers_env_then_config_then_default() {
        assert_eq!(
            choose_configured_port(Some("9123"), Some("[server]\nport = 9999\n")),
            9123
        );
        assert_eq!(
            choose_configured_port(None, Some("[server]\nport = 9999\n")),
            9999
        );
        assert_eq!(
            choose_configured_port(Some("not-a-port"), Some("[server]\nport = 9999\n")),
            9999
        );
        assert_eq!(
            choose_configured_port(None, Some("[server]\nport = \"bad\"\n")),
            DEFAULT_PORT
        );
        assert_eq!(origin_for_port(9123), "http://127.0.0.1:9123");
    }
}

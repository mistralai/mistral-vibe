//! Open external URLs without coupling the UI to a terminal feature.

use std::fs::OpenOptions;
use std::io::Write;
use std::process::{Command, Stdio};

const ACTION_LOG_ENV: &str = "VIBE_E2E_ACTION_LOG";
const SAFE_SCHEMES: [&str; 6] = [
    "http",
    "https",
    "vscode",
    "vscode-insiders",
    "cursor",
    "windsurf",
];

pub fn open(url: &str) {
    if !is_safe(url) {
        tracing::warn!(url, "refusing to open url");
        return;
    }
    open_checked(url);
}

pub fn open_file(url: &str) {
    let valid = !url.chars().any(char::is_control)
        && url::Url::parse(url)
            .ok()
            .filter(|parsed| parsed.scheme() == "file")
            .and_then(|parsed| parsed.to_file_path().ok())
            .is_some_and(|path| path.is_absolute());
    if !valid {
        tracing::warn!(url, "refusing to open attachment path");
        return;
    }
    open_checked(url);
}

fn open_checked(url: &str) {
    if record(url) {
        return;
    }
    if let Err(err) = open_system(url) {
        tracing::debug!(%err, url, "failed to open url");
    }
}

fn is_safe(url: &str) -> bool {
    let scheme = url.split_once(':').map(|(scheme, _)| scheme);
    matches!(scheme, Some(scheme) if SAFE_SCHEMES.iter().any(|safe| scheme.eq_ignore_ascii_case(safe)))
        && !url.chars().any(char::is_control)
}

fn record(url: &str) -> bool {
    let Some(path) = std::env::var_os(ACTION_LOG_ENV) else {
        return false;
    };
    let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) else {
        return true;
    };
    let action = serde_json::json!({"kind": "open_url", "url": url});
    let _ = serde_json::to_writer(&mut file, &action);
    let _ = writeln!(file);
    true
}

/// The TUI owns the terminal, so the opener never inherits its streams.
#[cfg(any(unix, target_os = "windows"))]
fn spawn_detached(mut command: Command) -> std::io::Result<()> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(drop)
}

#[cfg(target_os = "macos")]
fn open_system(url: &str) -> std::io::Result<()> {
    let mut command = Command::new("open");
    command.arg(url);
    spawn_detached(command)
}

#[cfg(target_os = "windows")]
fn open_system(url: &str) -> std::io::Result<()> {
    let mut command = Command::new("rundll32.exe");
    command.args(["url.dll,FileProtocolHandler", url]);
    spawn_detached(command)
}

#[cfg(all(unix, not(target_os = "macos")))]
fn open_system(url: &str) -> std::io::Result<()> {
    let mut command = Command::new("xdg-open");
    command.arg(url);
    spawn_detached(command)
}

#[cfg(not(any(target_os = "macos", target_os = "windows", unix)))]
fn open_system(_url: &str) -> std::io::Result<()> {
    Ok(())
}

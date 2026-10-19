//! POSIX theme probes.

use std::fs::File;
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::io::Read;
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::process::{Command, Stdio};
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::time::{Duration, Instant};

use super::{classify_osc11_response, is_complete_osc11_response, OSC11_QUERY};
use crate::tty_query;

#[cfg(any(target_os = "linux", target_os = "macos"))]
const OS_APPEARANCE_TIMEOUT: Duration = Duration::from_secs(1);

pub(super) fn detect_terminal_dark() -> Option<bool> {
    if tty_query::in_multiplexer() {
        return None;
    }
    detect_terminal_dark_from(tty_query::open()?)
}

pub fn detect_terminal_dark_from(mut tty: File) -> Option<bool> {
    classify_osc11_response(&tty_query::query(
        &mut tty,
        OSC11_QUERY,
        is_complete_osc11_response,
    )?)
}

#[cfg(target_os = "macos")]
pub(super) fn detect_system_preferred_dark() -> Option<bool> {
    let (success, stdout) = command_output("defaults", &["read", "-g", "AppleInterfaceStyle"])?;
    Some(super::classify_macos_appearance(success, &stdout))
}

#[cfg(target_os = "linux")]
pub(super) fn detect_system_preferred_dark() -> Option<bool> {
    let (success, stdout) = command_output(
        "gsettings",
        &["get", "org.gnome.desktop.interface", "color-scheme"],
    )?;
    super::classify_linux_appearance(success, &stdout)
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub(super) fn detect_system_preferred_dark() -> Option<bool> {
    None
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn command_output(program: &str, args: &[&str]) -> Option<(bool, String)> {
    let mut child = Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let deadline = Instant::now() + OS_APPEARANCE_TIMEOUT;
    let status = loop {
        if let Some(status) = child.try_wait().ok()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let mut stdout = String::new();
    child.stdout.take()?.read_to_string(&mut stdout).ok()?;
    Some((status.success(), stdout))
}

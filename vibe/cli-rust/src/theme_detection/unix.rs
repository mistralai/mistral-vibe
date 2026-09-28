//! POSIX theme probes.

use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, RawFd};
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use super::{classify_osc11_response, is_complete_osc11_response, OSC11_QUERY};

const OSC11_TIMEOUT: Duration = Duration::from_millis(500);
const MAX_RESPONSE_BYTES: usize = 256;
#[cfg(any(target_os = "linux", target_os = "macos"))]
const OS_APPEARANCE_TIMEOUT: Duration = Duration::from_secs(1);

pub(super) fn detect_terminal_dark() -> Option<bool> {
    if ["TMUX", "STY", "ZELLIJ"]
        .iter()
        .any(|name| std::env::var_os(name).is_some_and(|value| !value.is_empty()))
    {
        return None;
    }
    let tty = OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/tty")
        .ok()?;
    detect_terminal_dark_from(tty)
}

pub fn detect_terminal_dark_from(mut tty: File) -> Option<bool> {
    let fd = tty.as_raw_fd();
    let _guard = TermiosGuard::raw(fd)?;
    if readable(fd, Duration::ZERO).ok()? {
        return None;
    }
    tty.write_all(OSC11_QUERY).ok()?;
    tty.flush().ok()?;
    classify_osc11_response(&read_response(&mut tty).ok()?)
}

struct TermiosGuard {
    fd: RawFd,
    original: libc::termios,
}

impl TermiosGuard {
    fn raw(fd: RawFd) -> Option<Self> {
        let mut original = unsafe { std::mem::zeroed() };
        if unsafe { libc::tcgetattr(fd, &mut original) } != 0 {
            return None;
        }
        let mut raw = original;
        unsafe { libc::cfmakeraw(&mut raw) };
        if unsafe { libc::tcsetattr(fd, libc::TCSANOW, &raw) } != 0 {
            return None;
        }
        Some(Self { fd, original })
    }
}

impl Drop for TermiosGuard {
    fn drop(&mut self) {
        unsafe { libc::tcsetattr(self.fd, libc::TCSADRAIN, &self.original) };
    }
}

fn read_response(tty: &mut File) -> io::Result<Vec<u8>> {
    let deadline = Instant::now() + OSC11_TIMEOUT;
    let mut response = Vec::new();
    while response.len() < MAX_RESPONSE_BYTES && !is_complete_osc11_response(&response) {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() || !readable(tty.as_raw_fd(), remaining)? {
            break;
        }
        let mut byte = [0];
        if tty.read(&mut byte)? == 0 {
            break;
        }
        response.push(byte[0]);
    }
    Ok(response)
}

fn readable(fd: RawFd, timeout: Duration) -> io::Result<bool> {
    let deadline = Instant::now() + timeout;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        let mut read_fds = unsafe { std::mem::zeroed() };
        unsafe {
            libc::FD_ZERO(&mut read_fds);
            libc::FD_SET(fd, &mut read_fds);
        }
        let mut timeval = libc::timeval {
            tv_sec: remaining.as_secs() as libc::time_t,
            tv_usec: remaining.subsec_micros() as libc::suseconds_t,
        };
        let result = unsafe {
            libc::select(
                fd + 1,
                &mut read_fds,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut timeval,
            )
        };
        if result >= 0 {
            return Ok(result > 0 && unsafe { libc::FD_ISSET(fd, &read_fds) });
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted || remaining.is_zero() {
            return Err(error);
        }
    }
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

//! One-shot terminal queries on the tty, before the TUI reads input.

use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::time::{Duration, Instant};

const TIMEOUT: Duration = Duration::from_millis(500);
const MAX_RESPONSE_BYTES: usize = 256;

/// Multiplexers may not forward terminal queries or their replies.
pub fn in_multiplexer() -> bool {
    ["TMUX", "STY", "ZELLIJ"]
        .iter()
        .any(|name| std::env::var_os(name).is_some_and(|value| !value.is_empty()))
}

pub fn open() -> Option<File> {
    OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/tty")
        .ok()
}

/// Send `query` and read the reply until `complete`; `None` when input is already pending.
pub fn query(tty: &mut File, query: &[u8], complete: fn(&[u8]) -> bool) -> Option<Vec<u8>> {
    let fd = tty.as_raw_fd();
    let _guard = TermiosGuard::raw(fd)?;
    if readable(fd, Duration::ZERO).ok()? {
        return None;
    }
    tty.write_all(query).ok()?;
    tty.flush().ok()?;
    read_response(tty, complete).ok()
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

fn read_response(tty: &mut File, complete: fn(&[u8]) -> bool) -> io::Result<Vec<u8>> {
    let deadline = Instant::now() + TIMEOUT;
    let mut response = Vec::new();
    while response.len() < MAX_RESPONSE_BYTES && !complete(&response) {
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

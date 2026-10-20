//! Pty pair, relay pipe, exit decoding, and output tails.

use std::fs::File;
use std::os::unix::fs::PermissionsExt;

use std::os::fd::{AsRawFd, FromRawFd};

#[derive(Debug)]
pub enum Exit {
    Code(i32),
    Signal(i32),
}

impl Exit {
    /// Decode a raw waitpid status; panics on a stopped/continued report.
    pub fn from_status(status: i32) -> Self {
        if libc::WIFEXITED(status) {
            return Self::Code(libc::WEXITSTATUS(status));
        }
        assert!(
            libc::WIFSIGNALED(status),
            "raw waitpid status was neither exit nor signal: {status}"
        );
        Self::Signal(libc::WTERMSIG(status))
    }
}

impl Exit {
    /// `None` when the CLI died from a signal instead of exiting.
    pub fn code(&self) -> Option<i32> {
        match self {
            Self::Code(code) => Some(*code),
            Self::Signal(_) => None,
        }
    }

    pub fn signal(&self) -> Option<i32> {
        match self {
            Self::Code(_) => None,
            Self::Signal(signal) => Some(*signal),
        }
    }
}

pub fn pipe() -> (File, std::os::fd::OwnedFd) {
    let mut fds = [0; 2];
    assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0, "pipe failed");
    assert_eq!(
        unsafe { libc::fcntl(fds[0], libc::F_SETFD, libc::FD_CLOEXEC) },
        0
    );
    assert_eq!(
        unsafe { libc::fcntl(fds[0], libc::F_SETFL, libc::O_NONBLOCK) },
        0
    );
    unsafe {
        (
            File::from_raw_fd(fds[0]),
            std::os::fd::OwnedFd::from_raw_fd(fds[1]),
        )
    }
}

/// A blocking control pipe: its read end stays inheritable so the supervisor
/// keeps it, its write end stays with the driver.
pub fn pipe_blocking() -> (std::os::fd::OwnedFd, std::os::fd::OwnedFd) {
    let mut fds = [0; 2];
    assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0, "pipe failed");
    unsafe {
        (
            std::os::fd::OwnedFd::from_raw_fd(fds[0]),
            std::os::fd::OwnedFd::from_raw_fd(fds[1]),
        )
    }
}

pub fn openpty() -> (File, File) {
    let mut master = -1;
    let mut slave = -1;
    let mut size = libc::winsize {
        ws_row: 24,
        ws_col: 80,
        ws_xpixel: 0,
        ws_ypixel: 0,
    };
    assert_eq!(
        unsafe {
            libc::openpty(
                &mut master,
                &mut slave,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &raw mut size,
            )
        },
        0
    );
    let (master, slave) = unsafe { (File::from_raw_fd(master), File::from_raw_fd(slave)) };
    assert_eq!(
        unsafe { libc::fcntl(master.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) },
        0
    );
    assert_eq!(
        unsafe { libc::fcntl(master.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK) },
        0
    );
    (master, slave)
}

pub fn tail(output: &[u8]) -> String {
    let start = output.len().saturating_sub(512);
    String::from_utf8_lossy(&output[start..]).to_string()
}

const HOME_CONFIG: &str = "\
active_model = \"mistral-medium-3.5\"
enable_telemetry = false
enable_update_checks = false
disable_welcome_banner_animation = true

[[providers]]
name = \"mistral\"
api_base = \"https://api.mistral.ai/v1\"
api_key_env_var = \"MISTRAL_API_KEY\"
backend = \"mistral\"

[[models]]
name = \"mistral-vibe-cli\"
provider = \"mistral\"
alias = \"mistral-medium-3.5\"
";

/// The isolated VIBE_HOME contents: a minimal config and a pid-writing
/// replay wrapper. Returns the wrapper and the request-log paths.
pub struct HomePaths {
    pub replay_wrapper: std::path::PathBuf,
    pub requests_log: std::path::PathBuf,
}

pub fn setup_home(home: &std::path::Path) -> HomePaths {
    std::fs::write(home.join("config.toml"), HOME_CONFIG).expect("write config.toml");
    let wrapper = home.join("replay.sh");
    std::fs::write(
        &wrapper,
        format!(
            "#!/bin/sh\necho $$ > \"$VIBE_HOME/replay.pid\"\nexec \"{}\"\n",
            env!("CARGO_BIN_EXE_vibe-replay")
        ),
    )
    .expect("write replay wrapper");
    std::fs::set_permissions(&wrapper, std::fs::Permissions::from_mode(0o700)).expect("chmod");
    HomePaths {
        replay_wrapper: wrapper,
        requests_log: home.join("requests.log"),
    }
}

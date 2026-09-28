//! Terminal hangup must not strand the CLI or its app-server.

#![cfg(unix)]

use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

struct TerminalSession {
    child: Child,
    backend: i32,
    master: Option<File>,
    home: tempfile::TempDir,
    output: Vec<u8>,
}

impl TerminalSession {
    fn start(replaying: bool) -> Self {
        let home = tempfile::tempdir().unwrap();
        let backend = home.path().join("backend");
        std::fs::write(
            &backend,
            "#!/bin/sh\necho $$ > \"$VIBE_HOME/backend.pid\"\nexec cat >/dev/null\n",
        )
        .unwrap();
        std::fs::set_permissions(&backend, std::fs::Permissions::from_mode(0o700)).unwrap();
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
                    &mut size,
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
        let mut command = Command::new(env!("CARGO_BIN_EXE_vibe-rs"));
        command
            .current_dir(home.path())
            .env("VIBE_HOME", home.path())
            .env("VIBE_APP_SERVER_BIN", &backend)
            .env_remove("VIBE_APP_SERVER_CMD")
            .env_remove("VIBE_APP_SERVER_CWD")
            .env("TERM", "xterm-256color")
            .env_remove("SENTRY_DSN")
            .stdin(Stdio::from(slave.try_clone().unwrap()))
            .stdout(Stdio::from(slave.try_clone().unwrap()))
            .stderr(Stdio::from(slave));
        if replaying {
            command
                .env("VIBE_REPLAY_FIXTURE", "terminal-disconnect")
                .env("VIBE_REPLAY_BIN", &backend);
        } else {
            command
                .env_remove("VIBE_REPLAY_FIXTURE")
                .env_remove("VIBE_REPLAY_BIN");
        }
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 || libc::ioctl(0, libc::TIOCSCTTY as _, 0) == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = command.spawn().unwrap();
        let mut session = Self {
            child,
            backend: 0,
            master: Some(master),
            home,
            output: Vec::new(),
        };
        let pidfile = session.home.path().join("backend.pid");
        let deadline = Instant::now() + Duration::from_secs(5);
        while session.backend == 0 {
            session.drain();
            session.backend = std::fs::read_to_string(&pidfile)
                .ok()
                .and_then(|text| text.trim().parse().ok())
                .unwrap_or(0);
            assert!(Instant::now() < deadline, "app-server did not start");
            assert!(
                session.child.try_wait().unwrap().is_none(),
                "CLI exited before hangup"
            );
            thread::sleep(Duration::from_millis(10));
        }
        session.master.as_mut().unwrap().write_all(b"x").unwrap();
        let deadline = Instant::now() + Duration::from_millis(300);
        while Instant::now() < deadline {
            session.drain();
            thread::sleep(Duration::from_millis(10));
        }
        session
    }

    fn drain(&mut self) {
        let mut buffer = [0; 8192];
        while let Ok(size) = self.master.as_mut().unwrap().read(&mut buffer) {
            if size == 0 {
                break;
            }
            self.output.extend_from_slice(&buffer[..size]);
            assert!(
                self.output.len() < 1024 * 1024,
                "unexpected terminal output flood"
            );
        }
    }

    fn disconnect(&mut self) {
        drop(self.master.take());
        let deadline = Instant::now() + Duration::from_secs(5);
        while self.child.try_wait().unwrap().is_none() {
            assert!(
                Instant::now() < deadline,
                "CLI hung after terminal disconnect"
            );
            thread::sleep(Duration::from_millis(20));
        }
        let status = self.child.try_wait().unwrap().unwrap();
        assert!(status.code().is_some(), "CLI died from a signal: {status}");
        while unsafe { libc::kill(self.backend, 0) } == 0 {
            assert!(
                Instant::now() < deadline,
                "app-server survived terminal disconnect ({status})"
            );
            thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for TerminalSession {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        if self.backend > 0 {
            // Client::spawn makes the backend its process-group leader.
            unsafe {
                libc::kill(-self.backend, libc::SIGKILL);
            }
        }
    }
}

#[test]
fn disconnect_while_waiting_for_input_reaps_app_server() {
    TerminalSession::start(true).disconnect();
}

#[test]
fn startup_does_not_wait_for_keyboard_capability_responses() {
    let mut session = TerminalSession::start(false);
    assert!(!session.output.windows(4).any(|bytes| bytes == b"\x1b[?u"));
    assert!(session.output.windows(5).any(|bytes| bytes == b"\x1b[>1u"));
    session.disconnect();
}

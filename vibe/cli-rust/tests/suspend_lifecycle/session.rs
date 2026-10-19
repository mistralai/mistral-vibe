//! Driver side of the harness: PTY input in, supervisor events out.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use super::pty::{openpty, pipe, pipe_blocking, tail, Exit};
use super::supervisor::{Event, CLI_BIN_ENV, CONTROL_FD_ENV, EVENT_FD_ENV};

pub const CTRL_D: &[u8] = b"\x04";
pub const CTRL_Z: &[u8] = b"\x1a";
pub const SUSPEND_HINT: &[u8] = b"Mistral Vibe has been suspended";
/// The e2e harness's frozen `now` (config.py REPLAY_NOW_MS).
const REPLAY_NOW_MS: &str = "1_789_030_845_000";

pub struct Session {
    supervisor: Child,
    cli_pid: i32,
    backend: i32,
    master: Option<File>,
    events: File,
    unread: Vec<u8>,
    control: std::os::fd::OwnedFd,
    home: tempfile::TempDir,
    requests_path: std::path::PathBuf,
    output: Vec<u8>,
}

impl Session {
    /// PTY + supervisor + CLI, up to the point where the CLI pid is known.
    pub fn start() -> Self {
        let home = tempfile::tempdir().expect("temp home");
        let home_paths = super::pty::setup_home(home.path());
        let wrapper = home_paths.replay_wrapper;
        let requests_path = home_paths.requests_log;
        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../client-e2e/fixtures/fixture.json");

        let (events, event_w) = pipe();
        let (control_r, control) = pipe_blocking();
        let (master, slave) = openpty();
        let mut command = Command::new(std::env::current_exe().expect("test executable"));
        command
            .current_dir(home.path())
            .env("VIBE_HOME", home.path())
            .env("MISTRAL_API_KEY", "suspend-lifecycle-fake-key")
            .env("VIBE_TEST_DISABLE_KEYRING", "1")
            .env("TERM", "xterm-256color")
            .env("COLORTERM", "truecolor")
            .env("FORCE_COLOR", "1")
            .env("VIBE_THEME", "ansi-dark")
            .env("VIBE_REPLAY_NOW_MS", REPLAY_NOW_MS)
            .env("VIBE_REPLAY_FIXTURE", &fixture)
            .env("VIBE_REPLAY_BIN", &wrapper)
            .env("VIBE_REPLAY_REQUEST_LOG", &requests_path)
            .env(CLI_BIN_ENV, env!("CARGO_BIN_EXE_vibe-rs"))
            .env(EVENT_FD_ENV, event_w.as_raw_fd().to_string())
            .env(CONTROL_FD_ENV, control_r.as_raw_fd().to_string())
            .env_remove("VIBE_APP_SERVER_CMD")
            .env_remove("VIBE_APP_SERVER_BIN")
            .env_remove("VIBE_APP_SERVER_CWD")
            .env_remove("NO_COLOR")
            .env_remove("SENTRY_DSN")
            .args([
                "--ignored",
                "--exact",
                "helper_session_supervisor",
                "--nocapture",
            ])
            .stdin(Stdio::from(slave.try_clone().expect("clone stdin")))
            .stdout(Stdio::from(slave.try_clone().expect("clone stdout")))
            .stderr(Stdio::from(slave));
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 || libc::ioctl(0, libc::TIOCSCTTY as _, 0) == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let supervisor = command.spawn().expect("spawn supervisor");
        // The supervisor keeps its inherited copies; ours close so a dead
        // supervisor ends the relay stream (EOF) instead of hanging the driver.
        drop(event_w);
        drop(control_r);
        let mut session = Self {
            supervisor,
            cli_pid: 0,
            backend: 0,
            master: Some(master),
            events,
            unread: Vec::new(),
            control,
            home,
            requests_path,
            output: Vec::new(),
        };
        match session.next_event(Duration::from_secs(15)) {
            Some(Event::Started(pid)) => session.cli_pid = pid,
            other => panic!("supervisor never reported the CLI pid: {other:?}"),
        }
        let pidfile = session.home.path().join("replay.pid");
        let deadline = Instant::now() + Duration::from_secs(10);
        while session.backend == 0 {
            session.drain();
            session.backend = fs::read_to_string(&pidfile)
                .ok()
                .and_then(|text| text.trim().parse().ok())
                .unwrap_or(0);
            assert!(Instant::now() < deadline, "app-server did not start");
            thread::sleep(Duration::from_millis(10));
        }
        session
    }

    /// Wait until the handshake completed and a session exists, so a quit
    /// actually sends `session/stop`.
    pub fn wait_session_started(&mut self) {
        let deadline = Instant::now() + Duration::from_secs(30);
        while !self.requests().contains("session/start") {
            self.drain();
            assert!(
                Instant::now() < deadline,
                "handshake never completed; output tail: {:?}",
                tail(&self.output)
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    pub fn send(&mut self, bytes: &[u8]) {
        self.drain();
        self.master
            .as_mut()
            .expect("pty master")
            .write_all(bytes)
            .expect("write to pty");
    }

    /// Wait for a byte string the CLI printed to the restored terminal.
    pub fn wait_output(&mut self, needle: &[u8]) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !self.output.windows(needle.len()).any(|w| w == needle) {
            self.drain();
            self.assert_running();
            assert!(
                Instant::now() < deadline,
                "never saw {:?}; output tail: {:?}",
                String::from_utf8_lossy(needle),
                tail(&self.output)
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    /// Wait for the supervisor's report that the CLI actually stopped.
    pub fn wait_stopped(&mut self) {
        match self.next_event(Duration::from_secs(10)) {
            Some(Event::Stopped) => {}
            other => panic!("CLI never stopped after Ctrl+Z: {other:?}"),
        }
    }

    /// Wait for the CLI's post-SIGCONT repaint: re-entering the alternate
    /// screen (`EnterAlternateScreen` from `TerminalGuard::resume`) only
    /// happens after the signal handlers are restored, so stray pty noise
    /// from before the resume cannot satisfy this.
    pub fn wait_resumed(&mut self) {
        let baseline = self.output.len();
        let needle = b"\x1b[?1049h";
        let deadline = Instant::now() + Duration::from_secs(10);
        while !self.output[baseline..]
            .windows(needle.len())
            .any(|w| w == needle)
        {
            self.drain();
            self.assert_running();
            assert!(
                Instant::now() < deadline,
                "CLI never repainted after SIGCONT; output tail: {:?}",
                tail(&self.output)
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    /// The CLI's exit, decoded from the relayed raw waitpid status.
    pub fn wait_exit(&mut self, what: &str) -> Exit {
        let status = match self.next_event(Duration::from_secs(15)) {
            Some(Event::Exited(status)) => status,
            other => panic!("CLI did not exit after {what}: {other:?}"),
        };
        Exit::from_status(status)
    }

    /// Ctrl+D twice against the quit-confirmation banner, re-pressing the
    /// pair when a loaded machine let the one-second confirm window expire.
    pub fn quit_and_exit(&mut self, what: &str) -> Exit {
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            self.send(CTRL_D);
            self.wait_output(b"to quit");
            self.send(CTRL_D);
            let window = Instant::now() + Duration::from_secs(3);
            while let Some(event) =
                self.peek_event(window.saturating_duration_since(Instant::now()))
            {
                if let Event::Exited(status) = event {
                    return Exit::from_status(status);
                }
            }
        }
        panic!("Ctrl+D never quit the CLI ({what})");
    }

    /// One relayed event when it comes, `None` on a quiet pipe.
    fn peek_event(&mut self, timeout: Duration) -> Option<Event> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(event) = Event::decode(&mut self.unread) {
                return Some(event);
            }
            let mut buffer = [0; 64];
            match self.events.read(&mut buffer) {
                Ok(0) => return None,
                Ok(size) => self.unread.extend_from_slice(&buffer[..size]),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(error) => panic!("relay pipe read failed: {error}"),
            }
            if Instant::now() >= deadline {
                return None;
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    pub fn wait_backend_gone(&self) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while unsafe { libc::kill(self.backend, 0) } == 0 {
            assert!(
                Instant::now() < deadline,
                "app-server survived the CLI's death"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    /// Deliver a signal to the CLI, as a terminal close or `kill` would.
    pub fn signal(&self, sig: i32) {
        unsafe { libc::kill(self.cli_pid, sig) };
    }

    /// Like shell `bg` (`b`) or `fg` (`f`) for the stopped CLI job.
    pub fn job(&self, command: u8) {
        let fd = self.control.as_raw_fd();
        let mut file = unsafe { File::from_raw_fd(libc::dup(fd)) };
        file.write_all(&[command]).expect("send job command");
    }

    pub fn requests(&self) -> String {
        fs::read_to_string(&self.requests_path).unwrap_or_default()
    }

    fn assert_running(&mut self) {
        if let Ok(Some(status)) = self.supervisor.try_wait() {
            panic!("CLI session ended early: {status}");
        }
    }

    /// Poll the relay pipe until one decodable event arrives or the deadline.
    fn next_event(&mut self, timeout: Duration) -> Option<Event> {
        let deadline = Instant::now() + timeout;
        loop {
            self.drain();
            if let Some(event) = Event::decode(&mut self.unread) {
                return Some(event);
            }
            let mut buffer = [0; 64];
            match self.events.read(&mut buffer) {
                Ok(0) => return None,
                Ok(size) => self.unread.extend_from_slice(&buffer[..size]),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(error) => panic!("relay pipe read failed: {error}"),
            }
            assert!(
                Instant::now() < deadline,
                "timed out waiting for a CLI event; output tail: {:?}",
                tail(&self.output)
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn drain(&mut self) {
        let mut buffer = [0; 8192];
        while let Ok(size) = self.master.as_mut().expect("pty master").read(&mut buffer) {
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
}

impl Drop for Session {
    fn drop(&mut self) {
        // Close the pty first: a process mid-exit can be blocked flushing stdio
        // into a full pty, and the close turns that write into an EIO.
        drop(self.master.take());
        // A failing assertion must never leak processes into CI.
        if self.cli_pid > 0 {
            unsafe {
                libc::kill(-self.cli_pid, libc::SIGKILL);
                libc::kill(self.cli_pid, libc::SIGKILL);
            }
        }
        if self.backend > 0 {
            unsafe {
                libc::kill(-self.backend, libc::SIGKILL);
                libc::kill(self.backend, libc::SIGKILL);
            }
        }
        let _ = self.supervisor.kill();
        let _ = self.supervisor.wait();
    }
}

impl Session {
    /// Wait for any post-SIGCONT output; the signal handlers are already
    /// restored, so steady-loop frames prove the CLI runs again.
    pub fn wait_any_output(&mut self) {
        let baseline = self.output.len();
        let deadline = Instant::now() + Duration::from_secs(10);
        while self.output.len() <= baseline {
            self.drain();
            self.assert_running();
            assert!(
                Instant::now() < deadline,
                "CLI produced no output after SIGCONT; tail: {:?}",
                tail(&self.output)
            );
            thread::sleep(Duration::from_millis(10));
        }
    }
}

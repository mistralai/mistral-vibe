//! Owns the app-server child: grace wait and process-tree teardown.

use std::time::Duration;

use std::process::Stdio;

use anyhow::{Context, Result};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};

use super::Launch;

/// Grace period for the app-server to exit after `session/stop` before killing it.
pub const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);

/// Owns the app-server child process; `main.rs` waits on it after `session/stop`.
pub struct ChildHandle {
    pub(super) child: Child,
    // Retain the group after reaping its leader: tool descendants may survive it.
    #[cfg(unix)]
    pgid: Option<u32>,
    #[cfg(windows)]
    job: Option<super::windows_job::WindowsJob>,
}

impl ChildHandle {
    /// Launch the app-server with `extra_args` and `env`, its stderr drained to the log.
    pub(super) fn launch(
        launch: &Launch,
        extra_args: &[&str],
        env: &[(&str, &str)],
    ) -> Result<(Self, ChildStdin, ChildStdout)> {
        let mut cmd = Command::new(&launch.program);
        cmd.args(&launch.args)
            .args(extra_args)
            .envs(env.iter().copied())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            // Never inherited: the TUI owns the terminal, and anything the child
            // writes there corrupts cells ratatui does not know it must repaint.
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        // The child gets its own process group: the terminal sends Ctrl-C to
        // the whole foreground group, and a SIGINT landing on the app-server
        // mid-shutdown aborts it with a traceback. We drive its lifetime over
        // stdin instead.
        #[cfg(unix)]
        cmd.process_group(0);
        #[cfg(target_os = "linux")]
        die_with_parent(&mut cmd);
        if let Some(cwd) = &launch.cwd {
            cmd.current_dir(cwd);
        }
        // The app-server child loads ~/.vibe/.env itself (python-dotenv); the
        // keys this client's non-interpolating parser set would shadow the
        // child's own values, so strip exactly those.
        for key in crate::credentials::dotenv::dotenv_set_keys() {
            cmd.env_remove(&key);
        }

        let mut child = Self::spawn(&mut cmd)
            .with_context(|| format!("failed to spawn `{}`", launch.program))?;
        let stdin = child.child.stdin.take().context("child stdin missing")?;
        let stdout = child.child.stdout.take().context("child stdout missing")?;
        if let Some(stderr) = child.child.stderr.take() {
            tokio::spawn(super::stderr::drain(stderr));
        }
        Ok((child, stdin, stdout))
    }

    fn spawn(cmd: &mut Command) -> Result<Self> {
        #[cfg(windows)]
        let (child, job) = super::windows_job::WindowsJob::spawn(cmd)?;
        #[cfg(not(windows))]
        let child = cmd.spawn()?;
        Ok(Self {
            #[cfg(unix)]
            pgid: child.id(),
            child,
            #[cfg(windows)]
            job: Some(job),
        })
    }

    pub fn pid(&self) -> Option<u32> {
        self.child.id()
    }

    /// SIGKILL the child's process group now, for exit paths that bypass
    /// `Drop` (`process::exit` in the startup update dialog). On Windows the
    /// job object's handle closes at `process::exit`, killing the tree.
    pub fn kill_now(&self) {
        #[cfg(unix)]
        kill_group(self.pgid);
    }

    /// Wait for the child to exit, or kill its tree after `SHUTDOWN_GRACE`.
    /// Returns `Some` on self-exit, `None` on timeout or a failed wait.
    pub async fn wait_with_grace(mut self) -> Option<std::process::ExitStatus> {
        match tokio::time::timeout(SHUTDOWN_GRACE, self.child.wait()).await {
            Ok(status) => status.ok(),
            Err(_) => {
                self.kill_tree();
                let _ = self.child.kill().await;
                let _ = self.child.wait().await;
                None
            }
        }
    }

    fn kill_tree(&mut self) {
        #[cfg(unix)]
        kill_group(self.pgid);
        #[cfg(windows)]
        drop(self.job.take());
    }
}

impl Drop for ChildHandle {
    fn drop(&mut self) {
        self.kill_tree();
    }
}

/// Have the kernel SIGTERM the app-server once this client is gone, for exits
/// that run no `Drop`: SIGKILL, the OOM killer. SIGTERM, not SIGKILL: the
/// app-server then stops the tools it runs in sessions of their own, as on its
/// stdin's end, and bounds that shutdown itself; a SIGKILL would leave them
/// running. The signal follows the thread that spawned the child, not the
/// process; every app-server spawn runs on the CLI's one runtime thread, which
/// lives as long as the process. Elsewhere the app-server notices its stdin's
/// EOF instead, and a Windows job closes with its last handle.
#[cfg(target_os = "linux")]
fn die_with_parent(cmd: &mut Command) {
    let parent = std::process::id() as libc::pid_t;
    // SAFETY: runs in the forked child before exec, and makes only
    // async-signal-safe system calls.
    unsafe {
        cmd.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM as libc::c_ulong) == -1 {
                return Err(std::io::Error::last_os_error());
            }
            // The client ended before the prctl, so no signal will come.
            if libc::getppid() != parent {
                return Err(std::io::Error::from_raw_os_error(libc::ESRCH));
            }
            Ok(())
        });
    }
}

/// SIGKILL the child's whole process group, so tool descendants die with it.
#[cfg(unix)]
fn kill_group(pgid: Option<u32>) {
    if let Some(pid) = pgid {
        let _ = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
    }
}

//! Owns the app-server child: grace wait and process-tree teardown.

use std::time::Duration;

use anyhow::Result;
use tokio::process::{Child, Command};

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
    pub(super) fn spawn(cmd: &mut Command) -> Result<Self> {
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

/// SIGKILL the child's whole process group, so tool descendants die with it.
#[cfg(unix)]
fn kill_group(pgid: Option<u32>) {
    if let Some(pid) = pgid {
        let _ = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
    }
}

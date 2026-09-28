//! Owns the app-server child: grace wait and process-group teardown.

use std::time::Duration;

use tokio::process::Child;

/// Grace period for the app-server to exit after `session/stop` before SIGKILL.
pub const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);

/// Owns the app-server child process; `main.rs` waits on it after `session/stop`.
pub struct ChildHandle {
    child: Child,
    // Cached at spawn: `Child::id()` is None once reaped, but Drop still
    // needs the group after a normal exit (tool descendants may survive it).
    pgid: Option<u32>,
}

impl ChildHandle {
    pub(super) fn new(child: Child) -> Self {
        let pgid = child.id();
        Self { child, pgid }
    }

    pub fn pid(&self) -> Option<u32> {
        self.child.id()
    }

    /// Wait for the child to exit, or SIGKILL after `SHUTDOWN_GRACE`. The status
    /// is `Some` only when the child exited on its own, `None` when we killed it.
    pub async fn wait_with_grace(mut self) -> Option<std::process::ExitStatus> {
        match tokio::time::timeout(SHUTDOWN_GRACE, self.child.wait()).await {
            Ok(status) => status.ok(),
            Err(_) => {
                kill_group(self.pgid);
                let _ = self.child.kill().await;
                let _ = self.child.wait().await;
                None
            }
        }
    }
}

impl Drop for ChildHandle {
    fn drop(&mut self) {
        // kill_on_drop only signals the leader; tool descendants share its group.
        kill_group(self.pgid);
    }
}

/// SIGKILL the child's whole process group, so tool descendants die with it.
#[cfg(unix)]
fn kill_group(pgid: Option<u32>) {
    if let Some(pid) = pgid {
        let _ = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
    }
}

#[cfg(not(unix))]
fn kill_group(_pgid: Option<u32>) {}

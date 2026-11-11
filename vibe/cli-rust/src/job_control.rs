//! Ctrl+Z's job-wide stop, kept clear of children caught between fork and exec.

use std::process::{Child, Command};
use std::sync::{PoisonError, RwLock};

/// Spawns share it, the job stop takes it alone: on Linux a stop landing on a vfork child before exec never completes.
static SPAWNING: RwLock<()> = RwLock::new(());

/// `Command::spawn` for threads that may run while the event loop suspends.
pub fn spawn(command: &mut Command) -> std::io::Result<Child> {
    let _spawning = SPAWNING.read().unwrap_or_else(PoisonError::into_inner);
    command.spawn()
}

/// SIGTSTP to the whole job; returns once SIGCONT resumes it, `false` when the signal never went out.
#[cfg(unix)]
pub fn stop_job() -> bool {
    let _stopping = SPAWNING.write().unwrap_or_else(PoisonError::into_inner);
    unsafe { libc::kill(0, libc::SIGTSTP) == 0 }
}

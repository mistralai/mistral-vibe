//! OS signal forwarding for graceful shutdown.

use anyhow::{Context, Result};
use tokio::signal;

#[cfg(unix)]
use std::sync::atomic::{AtomicBool, Ordering};

#[cfg(unix)]
pub struct ShutdownSignal {
    term: signal::unix::Signal,
    int: signal::unix::Signal,
    hup: signal::unix::Signal,
}

/// Install before the TUI owns the terminal, so failure is a plain startup error.
#[cfg(unix)]
pub fn install() -> Result<ShutdownSignal> {
    use signal::unix::{signal, SignalKind};
    Ok(ShutdownSignal {
        term: signal(SignalKind::terminate()).context("install SIGTERM handler")?,
        int: signal(SignalKind::interrupt()).context("install SIGINT handler")?,
        hup: signal(SignalKind::hangup()).context("install SIGHUP handler")?,
    })
}

/// sigaction only fails for an invalid signal; a silent failure here would
/// leave a stopped CLI unable to die, so it stays an assertion.
#[cfg(unix)]
fn assert_set(result: i32) {
    assert_eq!(result, 0, "sigaction failed");
}

#[cfg(unix)]
static SUSPENDED: AtomicBool = AtomicBool::new(false);

/// SIGHUP/SIGTERM fall back to their default actions while the process is
/// stopped: a stopped process never runs handler code, so an installed
/// handler would pend until SIGCONT and a closed terminal would leave the
/// CLI alive forever (the Python TUI dies on the default action there).
#[cfg(unix)]
pub fn suspend_defaults() -> SuspendGuard {
    debug_assert!(
        !SUSPENDED.swap(true, Ordering::Relaxed),
        "a suspend while another is live would restore the default actions, not the handlers"
    );
    let mut hup: libc::sigaction = unsafe { std::mem::zeroed() };
    let mut term: libc::sigaction = unsafe { std::mem::zeroed() };
    let mut dfl: libc::sigaction = unsafe { std::mem::zeroed() };
    dfl.sa_sigaction = libc::SIG_DFL;
    // Each call installs the default action and captures the old one.
    unsafe {
        assert_set(libc::sigaction(libc::SIGHUP, &dfl, &raw mut hup));
        assert_set(libc::sigaction(libc::SIGTERM, &dfl, &raw mut term));
    }
    SuspendGuard {
        saved: Some((hup, term)),
    }
}

/// Owns the suspend window: restores the dispositions saved by
/// `suspend_defaults` on an explicit `rearm`, or on drop from any error path.
#[cfg(unix)]
pub struct SuspendGuard {
    saved: Option<(libc::sigaction, libc::sigaction)>,
}

#[cfg(unix)]
impl SuspendGuard {
    /// Restore the saved dispositions once the process owns the foreground
    /// again: the streams stay registered, only the kernel disposition
    /// changed. Re-registering instead would not work: signal-hook keeps its
    /// per-signal registration alive under tokio's global signal state, so a
    /// fresh `signal()` never rewrites the kernel disposition that `SIG_DFL`
    /// clobbered.
    pub fn rearm(mut self) {
        self.restore();
    }

    fn restore(&mut self) {
        if let Some((hup, term)) = self.saved.take() {
            unsafe {
                assert_set(libc::sigaction(libc::SIGHUP, &hup, std::ptr::null_mut()));
                assert_set(libc::sigaction(libc::SIGTERM, &term, std::ptr::null_mut()));
            }
        }
        SUSPENDED.store(false, Ordering::Relaxed);
    }
}

#[cfg(unix)]
impl Drop for SuspendGuard {
    fn drop(&mut self) {
        self.restore();
    }
}

#[cfg(unix)]
impl ShutdownSignal {
    pub async fn wait(&mut self) {
        tokio::select! {
            _ = self.term.recv() => {}
            _ = self.int.recv() => {}
            _ = self.hup.recv() => {}
        }
    }

    /// Like `wait`, but Ctrl+C belongs to a foreground child such as an editor.
    pub async fn wait_ignoring_interrupt(&mut self) {
        loop {
            tokio::select! {
                _ = self.term.recv() => return,
                _ = self.hup.recv() => return,
                _ = self.int.recv() => {}
            }
        }
    }
}

#[cfg(not(unix))]
pub struct ShutdownSignal {
    // A stream, not the one-shot ctrl_c() future: a prior signal must not ready wait().
    ctrl_c: signal::windows::CtrlC,
}

#[cfg(not(unix))]
pub fn install() -> Result<ShutdownSignal> {
    Ok(ShutdownSignal {
        ctrl_c: signal::windows::ctrl_c().context("install ctrl_c handler")?,
    })
}

#[cfg(not(unix))]
impl ShutdownSignal {
    pub async fn wait(&mut self) {
        let _ = self.ctrl_c.recv().await;
    }

    /// Like `wait`, but Ctrl+C belongs to a foreground child such as an editor.
    pub async fn wait_ignoring_interrupt(&mut self) {
        while self.ctrl_c.recv().await.is_some() {}
        std::future::pending::<()>().await;
    }
}

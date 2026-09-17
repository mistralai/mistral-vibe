//! OS signal forwarding for graceful shutdown.

use anyhow::{Context, Result};
use tokio::signal;

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

#[cfg(unix)]
impl ShutdownSignal {
    pub async fn wait(&mut self) {
        tokio::select! {
            _ = self.term.recv() => {}
            _ = self.int.recv() => {}
            _ = self.hup.recv() => {}
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
}

//! Ctrl+Z suspension of the whole TUI.

use anyhow::Result;

use crate::app::App;
use crate::terminal::TerminalGuard;

#[cfg(unix)]
use super::helpers::draw_synchronized;
#[cfg(unix)]
use crate::server::signal;

/// Suspend to the shell, print the fg hint, and re-enter the TUI on resume.
#[cfg(unix)]
pub(super) fn suspend_once(
    app: &mut App,
    terminal: &mut crate::terminal::Tui,
    guard: &mut TerminalGuard,
) -> Result<()> {
    guard.suspend();
    // Print the hint on the restored terminal before stopping.
    {
        use std::io::Write;
        let mut out = std::io::stdout();
        let _ = writeln!(
            out,
            "Mistral Vibe has been suspended. Run fg to bring Mistral Vibe back."
        );
        let _ = out.flush();
    }
    let suspend = signal::suspend_defaults();
    // kill returns only after SIGCONT resumes us; -1 means the signal never
    // went out and the process never stopped.
    let suspended = unsafe { libc::kill(0, libc::SIGTSTP) } == 0;
    // A backgrounded resume (`bg` instead of `fg`) stops us again by
    // SIGTTOU/SIGTTIN mid-call, and the next `bg` returns EINTR from the
    // interrupted terminal call; retry it like crossterm's own reader does.
    // While backgrounded the default dispositions must stay installed — a
    // stop with the handlers back would pend a kill or terminal close until
    // `fg` — so they are only restored once we own the foreground again.
    let foreground = unsafe { libc::tcgetpgrp(libc::STDIN_FILENO) } == unsafe { libc::getpgrp() };
    if foreground {
        suspend.rearm();
        retry_eintr::<()>(|| guard.resume().map_err(Into::into))?;
    } else {
        retry_eintr::<()>(|| guard.resume().map_err(Into::into))?;
        suspend.rearm();
    }
    app.terminal_notifier.invalidate_title();
    app.terminal_notifier.invalidate_program_status();
    retry_eintr::<()>(|| crate::terminal::repaint(terminal).map_err(Into::into))?;
    if !suspended {
        let id = format!("rs-suspend-failed-{}", app.view.transcript.revision());
        crate::transcript::local::add_notice(
            &mut app.view.transcript,
            &id,
            "Could not suspend the terminal; staying in the foreground.",
        );
    }
    retry_eintr::<()>(|| draw_synchronized(terminal, app))
}

/// Terminal calls interrupted by a stop/continue cycle (EINTR) are retried.
#[cfg(unix)]
fn retry_eintr<T>(mut call: impl FnMut() -> Result<T>) -> Result<T> {
    loop {
        match call() {
            Err(error) => {
                let interrupted = error.chain().any(|cause| {
                    cause
                        .downcast_ref::<std::io::Error>()
                        .is_some_and(|io| io.kind() == std::io::ErrorKind::Interrupted)
                });
                if interrupted {
                    continue;
                }
                return Err(error);
            }
            other => return other,
        }
    }
}

/// Platforms without job control stay in the TUI and never print an `fg` hint.
#[cfg(not(unix))]
pub(super) fn suspend_once(
    _app: &mut App,
    _terminal: &mut crate::terminal::Tui,
    _guard: &mut TerminalGuard,
) -> Result<()> {
    Ok(())
}

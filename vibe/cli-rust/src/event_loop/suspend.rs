//! Ctrl+Z suspension of the whole TUI.

use anyhow::Result;

use crate::app::App;
use crate::terminal::TerminalGuard;

#[cfg(unix)]
use super::helpers::draw_synchronized;

/// Suspend to the shell, print the fg hint, and re-enter the TUI on resume.
#[cfg(unix)]
pub(super) fn suspend_once(
    app: &mut App,
    terminal: &mut crate::terminal::Tui,
    guard: &mut TerminalGuard,
) -> Result<()> {
    guard.suspend();
    // Python prints the same hint on the restored terminal before stopping.
    {
        use std::io::Write;
        let mut out = std::io::stdout();
        let _ = writeln!(
            out,
            "Mistral Vibe has been suspended. Run fg to bring Mistral Vibe back."
        );
        let _ = out.flush();
    }
    // kill returns only after SIGCONT resumes us; -1 means the signal never
    // went out and the process never stopped.
    let suspended = unsafe { libc::kill(0, libc::SIGTSTP) } == 0;
    guard.resume()?;
    app.terminal_notifier.invalidate_title();
    terminal.clear()?;
    if !suspended {
        let id = format!("rs-suspend-failed-{}", app.view.transcript.revision());
        crate::transcript::local::add_notice(
            &mut app.view.transcript,
            &id,
            "Could not suspend the terminal; staying in the foreground.",
        );
    }
    draw_synchronized(terminal, app)
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

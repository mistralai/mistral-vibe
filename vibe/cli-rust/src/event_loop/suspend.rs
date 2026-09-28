//! Ctrl+Z suspension of the whole TUI.

use anyhow::Result;

use crate::app::App;
use crate::terminal::TerminalGuard;

use super::helpers::draw_synchronized;

/// Suspend to the shell, print the fg hint, and re-enter the TUI on resume.
pub(super) fn suspend_once(
    app: &mut App,
    terminal: &mut ratatui::DefaultTerminal,
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
    #[cfg(unix)]
    let suspended = unsafe { libc::kill(0, libc::SIGTSTP) } == 0;
    #[cfg(not(unix))]
    let suspended = true;
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

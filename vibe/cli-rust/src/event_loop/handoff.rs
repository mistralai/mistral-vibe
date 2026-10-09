//! Lend the terminal to a foreground child, such as an editor, and take it back.

use crate::terminal::{TerminalGuard, Tui};
use crate::terminal_events::{Paused, Reader};

/// The terminal and its input reader, which change hands together.
pub(super) struct TerminalHandoff<'a> {
    pub(super) terminal: &'a mut Tui,
    pub(super) guard: &'a mut TerminalGuard,
    pub(super) reader: &'a Reader,
}

/// The terminal while a child owns it; `end` or `abandon` takes it back.
pub(super) struct Lent<'a> {
    handoff: TerminalHandoff<'a>,
    paused: Paused,
}

impl<'a> TerminalHandoff<'a> {
    /// Stop reading input first, so no keystroke meant for the child is taken.
    pub(super) async fn begin(self) -> Lent<'a> {
        let paused = self.reader.pause().await;
        self.guard.suspend();
        Lent {
            handoff: self,
            paused,
        }
    }
}

impl Lent<'_> {
    /// Re-enter the TUI before reading input again; the next draw repaints everything.
    pub(super) fn end(self) -> std::io::Result<()> {
        let Lent { handoff, paused } = self;
        handoff.guard.resume()?;
        drop(paused);
        handoff.terminal.clear()
    }

    /// The run is ending: re-enter for teardown, keep the reader off for post-TUI prompts.
    pub(super) fn abandon(self) {
        let _ = self.handoff.guard.resume();
        std::mem::forget(self.paused);
    }
}

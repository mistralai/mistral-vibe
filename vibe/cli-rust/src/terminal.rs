//! Terminal mode lifecycle.

use crossterm::cursor::{Hide, Show};
use crossterm::event::{
    DisableBracketedPaste, DisableFocusChange, DisableMouseCapture, EnableBracketedPaste,
    EnableFocusChange, EnableMouseCapture, KeyboardEnhancementFlags, PopKeyboardEnhancementFlags,
    PushKeyboardEnhancementFlags,
};
use crossterm::execute;
use crossterm::terminal::{enable_raw_mode, EnterAlternateScreen};

pub struct TerminalGuard {
    active: bool,
    keyboard_pushed: bool,
}

pub fn init() -> std::io::Result<(ratatui::DefaultTerminal, TerminalGuard)> {
    // Own the terminal before arming the guard: a failed `try_init` must not
    // write teardown sequences to a terminal we never took over.
    let terminal = ratatui::try_init()?;
    let mut guard = TerminalGuard {
        active: true,
        keyboard_pushed: false,
    };
    if let Err(error) = guard.enter() {
        drop(guard);
        release(terminal);
        return Err(error);
    }
    Ok((terminal, guard))
}

/// Give the Ratatui terminal back, skipping its Drop when the terminal is gone:
/// Ratatui 0.29 panics there if showing the cursor and stderr both fail.
pub fn release(mut terminal: ratatui::DefaultTerminal) {
    if terminal.show_cursor().is_err() {
        std::mem::forget(terminal);
    }
}

impl TerminalGuard {
    fn enter(&mut self) -> std::io::Result<()> {
        self.enable_raw_modes()?;
        execute!(std::io::stdout(), crossterm::terminal::SetTitle("Vibe"))
    }

    fn enable_raw_modes(&mut self) -> std::io::Result<()> {
        execute!(
            std::io::stdout(),
            EnableMouseCapture,
            EnableBracketedPaste,
            EnableFocusChange
        )?;
        // Unsupported Unix terminals ignore the push; Windows rejects the command.
        if cfg!(unix) && std::env::var_os("VIBE_REPLAY_FIXTURE").is_none() {
            execute!(
                std::io::stdout(),
                PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
            )?;
            self.keyboard_pushed = true;
        }
        Ok(())
    }

    pub fn suspend(&mut self) {
        if !std::mem::take(&mut self.active) {
            return;
        }
        if std::mem::take(&mut self.keyboard_pushed) {
            let _ = execute!(std::io::stdout(), PopKeyboardEnhancementFlags);
        }
        crate::pointer::emit(crate::pointer::Shape::Default);
        let _ = execute!(
            std::io::stdout(),
            DisableFocusChange,
            DisableBracketedPaste,
            DisableMouseCapture
        );
        let _ = ratatui::try_restore();
        let _ = execute!(std::io::stdout(), Show, crossterm::terminal::SetTitle(""));
    }

    pub fn resume(&mut self) -> std::io::Result<()> {
        enable_raw_mode()?;
        // Raw mode is on, so teardown is owed even if the rest fails below.
        self.active = true;
        execute!(std::io::stdout(), EnterAlternateScreen, Hide)?;
        self.enable_raw_modes()
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        self.suspend();
    }
}

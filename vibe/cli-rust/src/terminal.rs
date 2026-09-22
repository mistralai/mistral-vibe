//! Terminal mode lifecycle.

use crossterm::cursor::{Hide, Show};
use crossterm::event::{
    DisableBracketedPaste, DisableFocusChange, DisableMouseCapture, EnableBracketedPaste,
    EnableFocusChange, EnableMouseCapture, KeyboardEnhancementFlags, PopKeyboardEnhancementFlags,
    PushKeyboardEnhancementFlags,
};
use crossterm::execute;
use crossterm::terminal::{enable_raw_mode, supports_keyboard_enhancement, EnterAlternateScreen};

pub struct TerminalGuard {
    keyboard_enhanced: bool,
}

pub fn init() -> (ratatui::DefaultTerminal, TerminalGuard) {
    let terminal = ratatui::init();
    enable_raw_modes();
    let _ = execute!(std::io::stdout(), crossterm::terminal::SetTitle("Vibe"));
    let replaying = std::env::var_os("VIBE_REPLAY_FIXTURE").is_some();
    let keyboard_enhanced = !replaying && supports_keyboard_enhancement().unwrap_or(false);
    if keyboard_enhanced {
        let _ = execute!(
            std::io::stdout(),
            PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
        );
    }
    (terminal, TerminalGuard { keyboard_enhanced })
}

fn enable_raw_modes() {
    let _ = execute!(
        std::io::stdout(),
        EnableMouseCapture,
        EnableBracketedPaste,
        EnableFocusChange
    );
}

fn disable_raw_modes() {
    let _ = execute!(
        std::io::stdout(),
        DisableFocusChange,
        DisableBracketedPaste,
        DisableMouseCapture
    );
    ratatui::restore();
    // ratatui::restore leaves the cursor hidden; the shell needs it back.
    let _ = execute!(std::io::stdout(), Show, crossterm::terminal::SetTitle(""));
}

impl TerminalGuard {
    /// Restore cooked mode before SIGTSTP so the shell prompt is usable.
    pub fn suspend(&mut self) {
        if self.keyboard_enhanced {
            let _ = execute!(std::io::stdout(), PopKeyboardEnhancementFlags);
        }
        disable_raw_modes();
    }

    /// Re-enter the TUI after SIGCONT, hidden cursor like the steady state.
    pub fn resume(&mut self) -> std::io::Result<()> {
        enable_raw_mode()?;
        execute!(std::io::stdout(), EnterAlternateScreen, Hide)?;
        enable_raw_modes();
        if self.keyboard_enhanced {
            execute!(
                std::io::stdout(),
                PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
            )?;
        }
        Ok(())
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        if self.keyboard_enhanced {
            let _ = execute!(std::io::stdout(), PopKeyboardEnhancementFlags);
        }
        crate::pointer::emit(crate::pointer::Shape::Default);
        disable_raw_modes();
    }
}

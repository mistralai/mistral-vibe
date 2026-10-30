//! Terminal mode lifecycle.

use crossterm::cursor::{Hide, Show};
use crossterm::event::{
    DisableBracketedPaste, DisableFocusChange, DisableMouseCapture, EnableBracketedPaste,
    EnableFocusChange, EnableMouseCapture, KeyboardEnhancementFlags, PopKeyboardEnhancementFlags,
    PushKeyboardEnhancementFlags,
};
use crossterm::execute;
use crossterm::style::Print;
use crossterm::terminal::{enable_raw_mode, EnterAlternateScreen};

use crate::resync_backend::ResyncBackend;

pub type Tui = ratatui::Terminal<ResyncBackend<std::io::Stdout>>;

pub struct TerminalGuard {
    active: bool,
    keyboard_pushed: bool,
    /// Mouse capture, following `enable_mouse_capture`; resume restores it.
    mouse: bool,
    /// Whether `ENABLE_WINDOW_INPUT` was added to the Windows console, to clear on suspend.
    window_input: bool,
}

/// Ratatui's `try_init`, with the resyncing backend.
pub fn try_init() -> std::io::Result<Tui> {
    let hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        ratatui::restore();
        hook(info);
    }));
    enable_raw_mode()?;
    execute!(std::io::stdout(), EnterAlternateScreen)?;
    Tui::new(ResyncBackend::new(std::io::stdout()))
}

/// Exit unless stdout, where the TUI draws, is a terminal (e.g. not `nohup vibe > /dev/null`).
pub fn require_interactive_terminal() {
    use std::io::IsTerminal;
    // No stdin check: crossterm reads keys from /dev/tty (CONIN$ on Windows) when stdin is not one.
    if std::io::stdout().is_terminal() {
        return;
    }
    eprintln!("Error: Interactive mode needs a terminal. Use -p/--prompt to run without one.");
    std::process::exit(1);
}

/// Own the terminal, capturing the mouse only when `mouse` (the cached `enable_mouse_capture`).
pub fn init(mouse: bool) -> std::io::Result<(Tui, TerminalGuard)> {
    // Own the terminal before arming the guard: a failed `try_init` must not
    // write teardown sequences to a terminal we never took over.
    let terminal = try_init()?;
    crate::mouse::pixels::set_capture(mouse);
    let mut guard = TerminalGuard {
        active: true,
        keyboard_pushed: false,
        mouse,
        window_input: false,
    };
    if let Err(error) = guard.enter() {
        drop(guard);
        release(terminal);
        return Err(error);
    }
    Ok((terminal, guard))
}

/// Capture the mouse. Crossterm's Windows capture only sets the console mode, so also request SGR
/// reports there: ConPTY decodes them, while legacy reports some terminals send arrive as typed keys.
pub fn capture_mouse(out: &mut impl std::io::Write) -> std::io::Result<()> {
    execute!(out, EnableMouseCapture)?;
    if cfg!(windows) {
        execute!(out, Print("\x1b[?1006h"))?;
    }
    Ok(())
}

/// Release the mouse, resetting Windows SGR reports even when restoring the console mode fails.
pub fn release_mouse(out: &mut impl std::io::Write) -> std::io::Result<()> {
    let released = execute!(out, DisableMouseCapture);
    if !cfg!(windows) {
        return released;
    }
    let sgr_reset = execute!(out, Print("\x1b[?1006l"));
    released.and(sgr_reset)
}

/// Turn SGR-pixels mouse reports on or off to match the window's cells.
pub fn sync_pixel_reports() -> std::io::Result<()> {
    match crate::mouse::pixels::sync() {
        Some(sequence) => execute!(std::io::stdout(), Print(sequence)),
        None => Ok(()),
    }
}

/// Hold cell mouse reports for a gesture, or go back to pixel reports after it.
pub fn hold_cell_reports(held: bool) {
    // Crate tests hold over a fake pixel grid: the terminal running them must not switch.
    if let Some(sequence) = crate::mouse::pixels::hold_cells(held).filter(|_| !cfg!(test)) {
        let _ = execute!(std::io::stdout(), Print(sequence));
    }
}

/// Draw `app`, then park the hidden cursor on its caret, else the static bottom-left cell.
pub fn draw(terminal: &mut Tui, app: &mut crate::app::App) -> std::io::Result<()> {
    let area = terminal.draw(|frame| app.draw(frame))?.area;
    let bottom_left = ratatui::layout::Position::new(area.x, area.bottom().saturating_sub(1));
    let caret = app.view.cursor_position.take().unwrap_or(bottom_left);
    terminal.set_cursor_position((
        caret.x.min(area.right().saturating_sub(1)),
        caret.y.min(area.bottom().saturating_sub(1)),
    ))
}

/// Clear the screen and force a full redraw. Unlike `Terminal::clear`, this sends no cursor-position query.
pub fn repaint<B: ratatui::backend::Backend>(
    terminal: &mut ratatui::Terminal<B>,
) -> Result<(), B::Error> {
    let area = terminal.size()?.into();
    terminal.resize(area)
}

/// Give the Ratatui terminal back, skipping its Drop when the terminal is gone:
/// Ratatui panics there if showing the cursor and stderr both fail.
pub fn release(mut terminal: Tui) {
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
        if self.mouse {
            capture_mouse(&mut std::io::stdout())?;
        } else {
            self.window_input = crate::platform::enable_window_input();
        }
        execute!(std::io::stdout(), EnableBracketedPaste, EnableFocusChange)?;
        sync_pixel_reports()?;
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

    /// Follow `enable_mouse_capture` live; while suspended, only `resume` applies it.
    pub fn set_mouse_capture(&mut self, enabled: bool) -> std::io::Result<()> {
        if enabled == self.mouse {
            return Ok(());
        }
        self.mouse = enabled;
        crate::mouse::pixels::set_capture(enabled);
        if !self.active {
            return Ok(());
        }
        if enabled {
            if std::mem::take(&mut self.window_input) {
                crate::platform::disable_window_input();
            }
            capture_mouse(&mut std::io::stdout())?;
            return sync_pixel_reports();
        }
        crate::pointer::emit(crate::pointer::Shape::Default);
        if crate::mouse::pixels::disable() {
            execute!(std::io::stdout(), Print(crate::mouse::pixels::DISABLE))?;
        }
        release_mouse(&mut std::io::stdout())?;
        self.window_input = crate::platform::enable_window_input();
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
        if crate::mouse::pixels::disable() {
            let _ = execute!(std::io::stdout(), Print(crate::mouse::pixels::DISABLE));
        }
        let _ = execute!(std::io::stdout(), DisableFocusChange, DisableBracketedPaste);
        if self.mouse {
            let _ = release_mouse(&mut std::io::stdout());
        }
        if std::mem::take(&mut self.window_input) {
            crate::platform::disable_window_input();
        }
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

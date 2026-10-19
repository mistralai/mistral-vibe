//! The debug console's scroll intents (keys and wheel) and click-copy.

use std::sync::atomic::Ordering;
use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};

use super::{at_top, load_page, view, DebugConsole};
use crate::app::{App, ToastSeverity};
use crate::server::Client;

/// A scroll intent from a key or the mouse wheel.
pub enum Scroll {
    Up,
    Down,
    PageUp,
    PageDown,
    Home,
}

/// The scroll a plain navigation key drives while the console is open, or
/// `None` to let the composer keep the key (the console never takes focus,
/// so rerouting these keys is deliberate).
pub fn scroll_for_key(key: &KeyEvent) -> Option<Scroll> {
    if !key.modifiers.is_empty() {
        return None;
    }
    match key.code {
        KeyCode::Up => Some(Scroll::Up),
        KeyCode::Down => Some(Scroll::Down),
        KeyCode::PageUp => Some(Scroll::PageUp),
        KeyCode::PageDown => Some(Scroll::PageDown),
        KeyCode::Home => Some(Scroll::Home),
        _ => None,
    }
}

/// Apply a scroll intent to the console's log.
pub fn scroll(app: &mut App, client: &Arc<Client>, intent: Scroll) {
    let page = app.debug_console.body_height.max(1);
    match intent {
        Scroll::Up => scroll_up(app, client, 1),
        Scroll::Down => scroll_down(app, 1),
        Scroll::PageUp => scroll_up(app, client, page),
        Scroll::PageDown => scroll_down(app, page),
        Scroll::Home => scroll_home(app, client),
    }
}

/// One wheel notch over the panel scrolls the log.
pub fn wheel(app: &mut App, client: &Arc<Client>, up: bool) {
    if up {
        scroll_up(app, client, crate::mouse::MOUSE_SCROLL_STEP);
    } else {
        scroll_down(app, crate::mouse::MOUSE_SCROLL_STEP);
    }
}

/// Scroll up by `lines`; at the top, load the previous page.
pub fn scroll_up(app: &mut App, client: &Arc<Client>, lines: u16) {
    let console = &mut app.debug_console;
    if !console.open {
        return;
    }
    console.scroll = console.scroll.saturating_add(lines);
    maybe_load_previous(app, client);
}

/// Scroll down by `lines`.
pub fn scroll_down(app: &mut App, lines: u16) {
    let console = &mut app.debug_console;
    if !console.open {
        return;
    }
    console.scroll = console.scroll.saturating_sub(lines);
}

/// Scroll to the oldest loaded line, loading the previous page.
pub fn scroll_home(app: &mut App, client: &Arc<Client>) {
    let console = &mut app.debug_console;
    if !console.open {
        return;
    }
    console.scroll = u16::MAX;
    // Canonicalize now: the render clamps it, but a degenerate panel that
    // never draws must not leave the offset stuck past the top.
    console.scroll = view(console).scroll;
    maybe_load_previous(app, client);
}

/// Load the previous page when the viewport is at the top and older entries exist.
pub fn maybe_load_previous(app: &mut App, client: &Arc<Client>) {
    let console = &app.debug_console;
    if !console.open
        || !console.has_more
        || console.cursor.is_none()
        || console.page_in_flight.load(Ordering::Relaxed)
        || !at_top(console)
    {
        return;
    }
    load_page(app, client);
}

/// The entry index painted at a screen row, for click-copy.
pub fn entry_at(console: &DebugConsole, row: u16) -> Option<usize> {
    console
        .hitmap
        .iter()
        .find(|(top, bottom, _)| row >= *top && row < *bottom)
        .map(|(_, _, index)| *index)
}

/// Copy a clicked line's plain text and toast.
pub fn copy_entry_at(app: &mut App, row: u16) {
    let Some(index) = entry_at(&app.debug_console, row) else {
        return;
    };
    let Some(entry) = app.debug_console.entries.get(index) else {
        return;
    };
    let text = entry.plain_line();
    crate::clipboard::copy_to_clipboard(&text);
    app.show_toast(
        "Copied to clipboard".to_string(),
        ToastSeverity::Information,
        2,
    );
}

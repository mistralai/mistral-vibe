//! Where composer keys and other input events reach vim mode.

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, MouseEventKind};

use super::handle;
use crate::app::App;

/// Send a composer `key` through vim mode when it owns it, handing back the `pending` operator; else to `composer`.
pub fn route(
    app: &mut App,
    key: KeyEvent,
    pending: Option<char>,
    mut composer: impl FnMut(&mut App, KeyEvent) -> bool,
) -> bool {
    if !owns(app, &key) {
        return composer(app, key);
    }
    app.vim.pending = pending;
    handle(app, key, composer)
}

/// Whether vim mode reads `key` before the composer: Esc first closes an open completion popup, digits rate feedback.
pub fn owns(app: &App, key: &KeyEvent) -> bool {
    app.vim.enabled
        && crate::input::composer_owns_key(app)
        && crate::input::composer_reachable(app)
        && !app.recording_active()
        && app.reverse_search.is_none()
        && !(key.code == KeyCode::Esc && crate::completion_manager::is_open(app))
        && !(app.feedback.message == crate::feedback::Message::Prompt
            && matches!(key.code, KeyCode::Char('0'..='3')))
}

/// Take the pending operator when `event` ends it: a key press, a click or a paste, not a mouse move.
pub fn take_pending(app: &mut App, event: &Event) -> Option<char> {
    let ends = match event {
        Event::Key(key) => key.kind == KeyEventKind::Press,
        Event::Mouse(mouse) => matches!(mouse.kind, MouseEventKind::Down(_)),
        _ => matches!(event, Event::Paste(_)),
    };
    app.vim.pending.take_if(|_| ends)
}

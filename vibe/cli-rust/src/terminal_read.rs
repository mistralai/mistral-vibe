//! The one terminal event read: every reader gets the platform-normalized event.

use std::io;

use crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};

use crate::platform::on_altgr_layer;

const ALTGR: KeyModifiers = KeyModifiers::CONTROL.union(KeyModifiers::ALT);

/// Read the next terminal event, normalized; the only caller of `crossterm::event::read`.
#[allow(clippy::disallowed_methods)]
pub fn read() -> io::Result<Event> {
    crossterm::event::read().map(normalize)
}

/// Normalize an event against the active keyboard layout.
pub fn normalize(event: Event) -> Event {
    normalize_on(event, on_altgr_layer)
}

/// Strip Ctrl+Alt from a character the layout types with AltGr; other Ctrl+Alt chords stay.
pub fn normalize_on(event: Event, on_altgr_layer: impl Fn(char) -> bool) -> Event {
    match event {
        Event::Key(
            key @ KeyEvent {
                code: KeyCode::Char(c),
                ..
            },
        ) if key.modifiers.contains(ALTGR) && on_altgr_layer(c) => Event::Key(KeyEvent {
            modifiers: key.modifiers - ALTGR,
            ..key
        }),
        event => event,
    }
}

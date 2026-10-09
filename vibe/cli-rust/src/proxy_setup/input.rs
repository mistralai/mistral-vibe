//! `/proxy-setup` keyboard, paste, clipboard, and pointer input.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::{cancel, save};
use crate::app::App;
use crate::chat_input::Action;
use crate::focus::Focus;
use crate::server::Client;

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let control = key.modifiers.contains(KeyModifiers::CONTROL);
    match key.code {
        KeyCode::Esc => cancel(app),
        KeyCode::Enter if key.modifiers.is_empty() => save(app, client),
        KeyCode::Up | KeyCode::Down => app.proxy_setup.move_focus(key.code == KeyCode::Down),
        KeyCode::Tab => app.proxy_setup.move_focus(true),
        KeyCode::Char('x') if control => {
            copy_selection(app, true);
        }
        KeyCode::Char('a' | 'A') if control && key.modifiers.contains(KeyModifiers::SHIFT) => {
            if let Some(field) = app.proxy_setup.focused_field() {
                field.edit(Action::SelectAll);
            }
        }
        _ => {
            app.proxy_setup.free_scroll = false;
            if let (Some(action), Some(field)) = (
                crate::keymap::action_for(&key),
                app.proxy_setup.focused_field(),
            ) {
                field.edit(action);
            }
        }
    }
}

/// Textual's `Input` keeps only the first line of a paste.
pub fn paste(app: &mut App, text: &str) {
    app.proxy_setup.free_scroll = false;
    let Some(field) = app.proxy_setup.focused_field() else {
        return;
    };
    for c in first_line(text).chars() {
        field.edit(Action::Insert(c));
    }
}

/// The first line of pasted text, splitting like Python's `str.splitlines`.
fn first_line(text: &str) -> &str {
    const BREAKS: [char; 10] = [
        '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
        '\u{2029}',
    ];
    text.find(BREAKS).map_or(text, |end| &text[..end])
}

/// Ctrl+C copies the focused selection (Textual `Input.copy`); true while the visible app owns the key.
pub fn copy_selection(app: &mut App, cut: bool) -> bool {
    if app.focus() != Focus::ProxySetup {
        return false;
    }
    let Some(field) = app.proxy_setup.focused_field() else {
        return true;
    };
    if let Some(text) = crate::chat_input::selected_text(&field.text, field.cursor, field.anchor) {
        crate::clipboard::copy_to_clipboard(&text);
        if cut {
            field.edit(Action::DeleteLeft);
        }
    }
    true
}

/// Mouse press: focus the input under the cursor and put the caret at the clicked column.
pub fn press(app: &mut App, at: (u16, u16)) {
    let state = &mut app.proxy_setup;
    let Some(&(index, area)) = state
        .input_areas
        .iter()
        .find(|(_, a)| a.contains(at.into()))
    else {
        return;
    };
    let column = at.0 - area.x;
    state.focused = index;
    state.free_scroll = false;
    let field = &mut state.inputs[index].field;
    field.cursor = field.byte_at(column as usize);
    field.anchor = None;
}

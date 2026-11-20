//! Rail keys: the focused rail's letters and steps, and the Ctrl+X and window-switch keys every rail surface shares.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::{back, enter, leave, open, painted, selected, switch, window_open};
use crate::app::App;
use crate::focus::Focus;
use crate::server::Client;

/// Whether `key` moves to the next (`true`) or previous (`false`) rail item: ←→ or Tab and Shift+Tab.
pub fn direction(key: &KeyEvent) -> Option<bool> {
    match key.code {
        KeyCode::BackTab => Some(false),
        KeyCode::Left if key.modifiers.is_empty() => Some(false),
        KeyCode::Right | KeyCode::Tab if key.modifiers.is_empty() => Some(true),
        _ => None,
    }
}

/// Ctrl+X always shows the rail's jump letters, wherever the rail's focus is.
pub fn is_toggle_key(key: &KeyEvent) -> bool {
    key.modifiers == KeyModifiers::CONTROL && key.code == KeyCode::Char('x')
}

/// Ctrl+X and ←→, Tab or Shift+Tab over an open window or the subagent list, not a child view: letter the rail, or switch windows.
pub fn nav_key(app: &mut App, client: &Arc<Client>, key: &KeyEvent) -> bool {
    // A child view is no rail surface: only Esc leaves it.
    let child_view = app.subagents.viewed_subagent_id.is_some();
    if !matches!(app.focus(), Focus::RailWindow | Focus::SubagentList) || child_view {
        return false;
    }
    if is_toggle_key(key) {
        enter(app);
        return true;
    }
    let Some(right) = direction(key) else {
        return false;
    };
    switch(app, client, right);
    true
}

/// Keys the focused rail owns; any other key leaves it and reaches the prompt.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: &KeyEvent) -> bool {
    // Any key after Ctrl+X hides the jump letters; Ctrl+X over them only hides them.
    let letters = std::mem::take(&mut app.rail.letters);
    if is_toggle_key(key) {
        if !letters {
            enter(app);
        }
        return true;
    }
    // Its last item left before the frame pruned it: Esc only leaves, any other key reaches the prompt.
    if super::items(app).is_empty() {
        leave(app);
        return key.code == KeyCode::Esc;
    }
    let window_open = window_open(app);
    let vim = match key.code {
        KeyCode::Char('h') if key.modifiers.is_empty() => Some(false),
        KeyCode::Char('l') if key.modifiers.is_empty() => Some(true),
        _ => None,
    };
    // ←→, Tab and Shift+Tab, or h/l walk the items; over an open window they switch windows, as they do inside it.
    if let Some(right) = direction(key).or(vim) {
        match window_open {
            true => switch(app, client, right),
            false => step(app, right),
        }
        return true;
    }
    if !key.modifiers.is_empty() || painted(app).is_empty() {
        back(app);
        return window_open;
    }
    match key.code {
        KeyCode::Enter | KeyCode::Char(' ') => {
            if let Some(item) = selected(app) {
                open(app, client, item);
            }
        }
        KeyCode::Esc | KeyCode::Down => back(app),
        KeyCode::Char(letter) if jump(app, client, letter) => {}
        _ => {
            back(app);
            return window_open;
        }
    }
    true
}

/// Open the item `letter` names, if the rail shows it.
fn jump(app: &mut App, client: &Arc<Client>, letter: char) -> bool {
    let Some(item) = painted(app)
        .into_iter()
        .find(|item| item.letter() == letter)
    else {
        return false;
    };
    open(app, client, item);
    true
}

/// Move the highlight one item, wrapping at both ends.
fn step(app: &mut App, right: bool) {
    let items = painted(app);
    let Some(current) = selected(app).and_then(|item| items.iter().position(|i| *i == item)) else {
        return;
    };
    let next = match right {
        true => (current + 1) % items.len(),
        false => (current + items.len() - 1) % items.len(),
    };
    app.rail.selected = Some(items[next]);
}

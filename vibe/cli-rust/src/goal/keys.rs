//! Goal panel keys and paste, and the resume prompt's digits.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::{format, request, Panel};
use crate::app::App;
use crate::chat_input::Action;
use crate::server::{Client, GoalStatus};

/// Keys of the open goal panel.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let plain = (key.modifiers - KeyModifiers::SHIFT).is_empty();
    let nested = match &app.goal.panel {
        Some(Panel::Edit { nested, .. }) => *nested,
        Some(Panel::Confirm(confirm)) => confirm.nested,
        _ => false,
    };
    match app.goal.panel.as_mut() {
        None => {}
        Some(Panel::Detail) => detail_key(app, client, key, plain),
        Some(Panel::Edit { form, .. }) => match key.code {
            KeyCode::Esc => back(app, nested),
            KeyCode::Tab | KeyCode::BackTab => form.switch_field(),
            KeyCode::Enter if key.modifiers.is_empty() => save(app, client),
            _ => form.edit(&key),
        },
        Some(Panel::Confirm(_)) => match key.code {
            KeyCode::Esc => back(app, nested),
            KeyCode::Up | KeyCode::Char('k') if plain => super::step(app, false),
            KeyCode::Down | KeyCode::Char('j') if plain => super::step(app, true),
            KeyCode::Enter => super::choose(app, client),
            _ => {}
        },
    }
}

fn detail_key(app: &mut App, client: &Arc<Client>, key: KeyEvent, plain: bool) {
    let Some(goal) = app.goal.goal.clone() else {
        app.goal.panel = None;
        return;
    };
    match key.code {
        KeyCode::Esc => app.goal.panel = None,
        KeyCode::Char('p') if plain && goal.status == GoalStatus::Active => {
            app.goal.panel = None;
            super::set_status(app, client, "paused");
        }
        KeyCode::Char('p') if plain && format::resumable(goal.status) => {
            app.goal.panel = None;
            super::set_status(app, client, "active");
        }
        KeyCode::Char('e') if plain => super::open_edit(app, &goal, true),
        KeyCode::Char('c') if plain => super::clear(app, client, &goal, true),
        _ => {}
    }
}

/// Keys of the rail's goal window: the detail panel's actions; edit and clear open their panels over it.
pub fn window_key(app: &mut App, client: &Arc<Client>, key: &KeyEvent) {
    let Some(goal) = app.goal.goal.clone() else {
        return;
    };
    if !(key.modifiers - KeyModifiers::SHIFT).is_empty() {
        return;
    }
    match key.code {
        KeyCode::Char('p') if goal.status == GoalStatus::Active => {
            super::set_status(app, client, "paused")
        }
        KeyCode::Char('p') if format::resumable(goal.status) => {
            super::set_status(app, client, "active")
        }
        KeyCode::Char('e') => super::open_edit(app, &goal, false),
        KeyCode::Char('c') => super::clear(app, client, &goal, false),
        _ => {}
    }
}

/// Esc: a nested panel goes back to the detail panel, a top-level one closes.
fn back(app: &mut App, nested: bool) {
    app.goal.panel = nested.then_some(Panel::Detail);
}

/// Send the edit; the form closes, and comes back with what was typed if the server rejects it.
fn save(app: &mut App, client: &Arc<Client>) {
    let Some(Panel::Edit { form, .. }) = app.goal.panel.as_mut() else {
        return;
    };
    match form.params() {
        Ok(params) => {
            let panel = app.goal.panel.take();
            request::send_set(app, client, params, panel);
        }
        Err(error) => form.error = Some(error),
    }
}

/// Paste into the focused edit field; the budget keeps the first line only.
pub fn paste(app: &mut App, text: &str) {
    let Some(Panel::Edit { form, .. }) = app.goal.panel.as_mut() else {
        return;
    };
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let text = match form.budget_focused {
        true => text.lines().next().unwrap_or_default().to_owned(),
        false => text,
    };
    for c in text.chars() {
        form.apply(Action::Insert(c));
    }
}

/// Whether `key` answers the resume prompt: `1`/`2` while the composer is empty.
pub fn is_prompt_key(app: &App, key: &KeyEvent) -> bool {
    app.goal.resume_prompt
        && key.modifiers.is_empty()
        && matches!(key.code, KeyCode::Char('1' | '2'))
        && app.chat_input.full_text().is_empty()
}

/// Answer the resume prompt with `key`; true when consumed.
pub fn handle_prompt_key(app: &mut App, client: &Arc<Client>, key: &KeyEvent) -> bool {
    if !is_prompt_key(app, key) {
        return false;
    }
    app.goal.resume_prompt = false;
    if key.code == KeyCode::Char('1') {
        super::set_status(app, client, "active");
    }
    true
}

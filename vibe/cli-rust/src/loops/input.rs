//! `/loop` manager keys and paste: the list's, or the add form's while it is open.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::{close, create, delete, navigate, Form, FormField};
use crate::app::App;
use crate::chat_input::Action;
use crate::server::Client;
use crate::utils::text::{first_line, multi_line};

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if app.loops.form.is_some() {
        return form_key(app, client, key);
    }
    let plain = !key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    match key.code {
        KeyCode::Esc => close(app),
        KeyCode::Up => navigate(app, false),
        KeyCode::Down => navigate(app, true),
        KeyCode::Char('k') if plain => navigate(app, false),
        KeyCode::Char('j') if plain => navigate(app, true),
        KeyCode::Char('d') if plain => delete(app, client),
        KeyCode::PageUp | KeyCode::PageDown => {
            let details = &mut app.loops.details;
            details.scroll_by(key.code == KeyCode::PageDown, details.visible.max(1));
        }
        KeyCode::Char('n') if plain => {
            app.loops.error = None;
            app.loops.form = Some(Form::default());
        }
        _ => {}
    }
}

fn form_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let Some(form) = app.loops.form.as_mut() else {
        return;
    };
    match key.code {
        KeyCode::Esc => {
            app.loops.form = None;
            app.loops.error = None;
        }
        KeyCode::Up | KeyCode::Down => {
            let down = key.code == KeyCode::Down;
            if !form.move_caret(down) {
                form.move_focus(down);
            }
        }
        KeyCode::Tab => form.move_focus(true),
        KeyCode::BackTab => form.move_focus(false),
        KeyCode::Enter if key.modifiers.is_empty() => submit(app, client),
        _ => {
            if let Some(action) = crate::keymap::action_for(&key) {
                form.edit(action);
            }
        }
    }
}

fn submit(app: &mut App, client: &Arc<Client>) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let Some(form) = app.loops.form.as_mut() else {
        return;
    };
    if form.submission.is_some() {
        return;
    }
    let params = match form.params(&session_id) {
        Ok(params) => params,
        Err(error) => return app.loops.error = Some(error),
    };
    if create(app, client, session_id, params) {
        let submission = app.loops.submissions;
        if let Some(form) = app.loops.form.as_mut() {
            form.submission = Some(submission);
        }
    }
}

/// A paste keeps every line in the instructions, and its first line elsewhere.
pub fn paste(app: &mut App, text: &str) {
    let Some(form) = app.loops.form.as_mut() else {
        return;
    };
    let text = match form.focused_field() {
        FormField::Instructions => multi_line(text),
        _ => first_line(text).to_owned(),
    };
    for c in text.chars() {
        form.edit(Action::Insert(c));
    }
}

//! `/config` settings screen state and value formatting.

mod dynamic;
mod voice_toggle;

use crate::server::method;
use crate::server::Client;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::mpsc;

use crate::app::{App, ToastSeverity};
pub use crate::config_fields::{ConfigField, Loaded};
use crate::model_picker;
use crate::search_field::{self, Outcome, Search};
pub fn open(app: &mut App, client: &Arc<Client>, tx: &mpsc::Sender<Loaded>) {
    app.config_screen.fields.clear();
    app.config_screen.list.reset();
    app.config_screen.search = Search::default();
    app.config_screen.edit = None;
    app.config_screen.loading = true;
    app.config_screen.open = true;
    if let Some(session_id) = app.session.session_id.clone() {
        load(session_id, client, tx, true);
    }
}
/// Read the fields; a failed read is reported, or dropped when `report` is off.
pub fn load(session_id: String, client: &Arc<Client>, tx: &mpsc::Sender<Loaded>, report: bool) {
    let client = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let params = serde_json::json!({ "sessionId": session_id });
        let loaded = match client.request(method::CONFIG_FIELDS_READ, params).await {
            Ok(value) => crate::config_fields::parse(&value),
            Err(error) => Loaded {
                error: Some(crate::server::request_error::describe(&error)),
                ..Loaded::default()
            },
        };
        if report || loaded.error.is_none() {
            let _ = tx.send(loaded).await;
        }
    });
}
pub fn apply_loaded(app: &mut App, mut loaded: Loaded) {
    if let Some(error) = loaded.error.take() {
        app.config_screen.loading = false;
        if app.config_screen.open {
            let message = format!("Failed to load settings: {error}");
            let secs = model_picker::FAILED_TOAST_SECS;
            app.show_toast(message, ToastSeverity::Error, secs);
        }
        return;
    }
    voice_toggle::record(app, &loaded);
    let index = app.config_screen.list.selected();
    let path = filtered(app).get(index).map(|field| field.path.clone());
    for field in &mut loaded.fields {
        dynamic::apply_choices(app, field);
    }
    app.config_screen.fields = loaded.fields;
    app.config_screen.targets = loaded.targets;
    let fields = filtered(app);
    app.config_screen.list.highlight(
        path.and_then(|path| fields.iter().position(|field| field.path == path))
            .unwrap_or_else(|| index.min(fields.len().saturating_sub(1))),
    );
    app.config_screen.loading = false;
}

pub fn filtered(app: &App) -> Vec<&ConfigField> {
    use crate::utils::fuzzy;
    use std::cmp::Reverse;

    let query = app.config_screen.search.query.trim();
    if query.is_empty() {
        let mut fields: Vec<_> = app.config_screen.fields.iter().collect();
        fields.sort_by_key(|field| !field.popular);
        return fields;
    }
    let mut scored: Vec<_> = app
        .config_screen
        .fields
        .iter()
        .filter_map(|field| {
            let name = fuzzy::score(query, &field.name).unwrap_or(0);
            let description = fuzzy::score(query, &field.description).unwrap_or(0);
            // Names carry twice the description weight, without rounding.
            let score = (name * 2).max(description);
            (score > 0).then_some((score, field))
        })
        .collect();
    if scored.len() <= 5 {
        // Popular fields get a 1.25x boost only in the merged list.
        scored.sort_by_key(|(score, field)| Reverse(score * if field.popular { 5 } else { 4 }));
    } else {
        scored.sort_by_key(|(score, field)| (!field.popular, Reverse(*score)));
    }
    scored.into_iter().map(|(_, field)| field).collect()
}

pub fn handle_key(app: &mut App, client: &Arc<Client>, tx: &mpsc::Sender<Loaded>, key: KeyEvent) {
    if app.config_screen.edit.is_some() {
        crate::config_edit::handle_key(app, client, tx, key);
        return;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('r') {
        reset_selected(app, client, tx);
        return;
    }
    match search_field::handle_key(&mut app.config_screen.search, &key) {
        Outcome::Pass => {}
        Outcome::Consumed => return,
        Outcome::Filtered => return query_changed(app),
    }
    let plain = !key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    match key.code {
        KeyCode::Esc => close(app),
        KeyCode::Up => step(app, false),
        KeyCode::Down => step(app, true),
        KeyCode::Char('k') if plain => step(app, false),
        KeyCode::Char('j') if plain => step(app, true),
        KeyCode::PageUp => move_by(app, -10),
        KeyCode::PageDown => move_by(app, 10),
        KeyCode::Enter => edit_selected(app),
        _ => {}
    }
}

fn query_changed(app: &mut App) {
    app.config_screen.list.reset();
}

pub fn handle_mouse(
    app: &mut App,
    client: &Arc<Client>,
    tx: &mpsc::Sender<Loaded>,
    event: MouseEvent,
) {
    if app.config_screen.edit.is_some() {
        match event.kind {
            MouseEventKind::Down(MouseButton::Left) | MouseEventKind::Up(MouseButton::Left)
                if crate::config_edit::mouse(app, event) =>
            {
                crate::config_edit::commit(app, client, tx)
            }
            _ => {}
        }
        return;
    }
    if event.kind == MouseEventKind::Down(MouseButton::Left) {
        let at = (event.column, event.row);
        let screen = &mut app.config_screen;
        if screen.search.area.contains(at.into()) {
            screen.search.focused = true;
            return;
        }
        if screen.list.index_at(at).is_some() {
            screen.search.focused = false;
        }
    }
    if app.config_screen.list.confirmed(event).is_some() {
        edit_selected(app);
    }
}

pub fn handle_paste(app: &mut App, text: String) {
    if app.config_screen.edit.is_some() {
        crate::config_edit::paste(app, text);
        return;
    }
    if search_field::paste(&mut app.config_screen.search, &text) {
        query_changed(app);
    }
}

fn close(app: &mut App) {
    app.config_screen.open = false;
    app.config_screen.search = Search::default();
    app.config_screen.edit = None;
    crate::selection::scrolled(app);
}

/// ↑↓/jk: one row, wrapping at the ends.
fn step(app: &mut App, down: bool) {
    let count = filtered(app).len();
    app.config_screen.list.step(down, count, true, |_| true);
}

/// PageUp/PageDown: a page of rows, clamped at the ends.
fn move_by(app: &mut App, delta: isize) {
    let count = filtered(app).len();
    if count == 0 {
        return;
    }
    let list = &mut app.config_screen.list;
    list.select(list.selected().saturating_add_signed(delta).min(count - 1));
}

fn selected(app: &App) -> Option<ConfigField> {
    filtered(app)
        .get(app.config_screen.list.selected())
        .cloned()
        .cloned()
}

fn edit_selected(app: &mut App) {
    let Some(field) = selected(app) else { return };
    if !field.writable {
        return;
    }
    crate::config_edit::open(app, field);
}

fn reset_selected(app: &mut App, client: &Arc<Client>, tx: &mpsc::Sender<Loaded>) {
    let Some(field) = selected(app) else { return };
    if !field.writable || !field.overridden {
        return;
    }
    let target = field
        .layers
        .first()
        .map(|(layer, _)| layer.clone())
        .unwrap_or_default();
    if !app.config_screen.targets.contains(&target) {
        app.show_toast(
            format!(
                "'{}' is pinned by {}; nothing to clear.",
                field.name,
                origin_label(&target)
            ),
            ToastSeverity::Information,
            5,
        );
        return;
    }
    write(app, client, tx, field, "remove", Value::Null, target);
}

pub(crate) fn origin_label(origin: &str) -> String {
    match origin {
        "default" => "defaults".to_owned(),
        "overrides" => "temporary".to_owned(),
        "environment" => "env".to_owned(),
        "admin" => "your administrator".to_owned(),
        origin if origin.ends_with("-toml") => {
            format!("{} config", origin.trim_end_matches("-toml"))
        }
        origin => origin.to_owned(),
    }
}

pub(crate) fn write(
    app: &App,
    client: &Arc<Client>,
    tx: &mpsc::Sender<Loaded>,
    field: ConfigField,
    op: &str,
    value: Value,
    target: String,
) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    let tx = tx.clone();
    let events_tx = app.events.clone();
    let pending = app.commit_started();
    let op = op.to_owned();
    tokio::spawn(async move {
        let ops = vec![serde_json::json!(
            {"op": op, "path": field.path, "value": value, "targetLayer": target}
        )];
        let reason = if op == "set" {
            "config screen edit"
        } else {
            "config screen reset"
        };
        let event = match crate::config_write::write(&client, &session_id, ops, reason).await {
            Ok(result) => model_picker::Event::Written(result),
            Err(err) => {
                tracing::warn!(%err, path = %field.path, "settings update failed");
                let verb = if op == "set" { "save" } else { "reset" };
                let layer = origin_label(&target);
                model_picker::Event::Failed(format!(
                    "Failed to {verb} {} ({layer}): {err}",
                    field.name
                ))
            }
        };
        let failed = matches!(event, model_picker::Event::Failed(_));
        crate::input::deliver(events_tx, event, &pending).await;
        // A failed write may still have moved layers; its toast already reports the failure.
        load(session_id, &client, &tx, !failed);
    });
}

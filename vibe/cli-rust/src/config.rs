//! `/config` settings screen state and value formatting.

use crate::server::method;
use crate::server::Client;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::mpsc;

use crate::app::{App, ToastSeverity};
pub use crate::config_fields::{ConfigField, Loaded};
use crate::model_picker;
pub fn open(app: &mut App, client: &Arc<Client>, tx: &mpsc::Sender<Loaded>) {
    app.config_screen.fields.clear();
    app.config_screen.selected = 0;
    app.config_screen.scroll = 0;
    app.config_screen.free_scroll = false;
    app.config_screen.query.clear();
    app.config_screen.edit = None;
    app.config_screen.loading = true;
    app.config_screen.open = true;
    if let Some(session_id) = app.session.session_id.clone() {
        load(session_id, client, tx);
    }
}
pub fn load(session_id: String, client: &Arc<Client>, tx: &mpsc::Sender<Loaded>) {
    let client = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let params = serde_json::json!({ "sessionId": session_id });
        let loaded = client
            .request(method::CONFIG_FIELDS_READ, params)
            .await
            .map_or_else(
                |_| Loaded {
                    fields: Vec::new(),
                    targets: Vec::new(),
                },
                |value| crate::config_fields::parse(&value),
            );
        let _ = tx.send(loaded).await;
    });
}
pub fn filtered(app: &App) -> Vec<&ConfigField> {
    let query = app.config_screen.query.trim().to_lowercase();
    let matches: Vec<&ConfigField> = app
        .config_screen
        .fields
        .iter()
        .filter(|field| query.is_empty() || field.name.to_lowercase().contains(&query))
        .collect();
    matches
        .iter()
        .copied()
        .filter(|field| field.popular)
        .chain(matches.iter().copied().filter(|field| !field.popular))
        .collect()
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
    match key.code {
        KeyCode::Esc => close(app),
        KeyCode::Up => move_by(app, -1),
        KeyCode::Down => move_by(app, 1),
        KeyCode::PageUp => move_by(app, -10),
        KeyCode::PageDown => move_by(app, 10),
        KeyCode::Backspace => {
            app.config_screen.query.pop();
            app.config_screen.selected = 0;
            app.config_screen.scroll = 0;
            app.config_screen.free_scroll = false;
        }
        KeyCode::Char(c)
            if !key
                .modifiers
                .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
        {
            app.config_screen.query.push(c);
            app.config_screen.selected = 0;
            app.config_screen.scroll = 0;
            app.config_screen.free_scroll = false;
        }
        KeyCode::Enter => edit_selected(app),
        _ => {}
    }
}

pub fn handle_mouse(
    app: &mut App,
    client: &Arc<Client>,
    tx: &mpsc::Sender<Loaded>,
    event: MouseEvent,
) {
    if app.config_screen.edit.is_some() {
        match event.kind {
            MouseEventKind::Down(MouseButton::Left)
                if crate::config_edit::click(app, event.column, event.row) =>
            {
                crate::config_edit::commit(app, client, tx)
            }
            _ => {}
        }
        return;
    }
    if let MouseEventKind::Down(MouseButton::Left) = event.kind {
        if let Some(index) = crate::config_mouse::row_at(app, event.column, event.row) {
            app.config_screen.selected = index;
        }
    }
}

pub fn handle_paste(app: &mut App, text: String) {
    if app.config_screen.edit.is_some() {
        crate::config_edit::paste(app, text);
        return;
    }
    app.config_screen.query.push_str(&text);
    app.config_screen.selected = 0;
    app.config_screen.scroll = 0;
    app.config_screen.free_scroll = false;
}

fn close(app: &mut App) {
    app.config_screen.open = false;
    app.config_screen.query.clear();
    app.config_screen.edit = None;
    crate::selection::scrolled(app);
}

fn move_by(app: &mut App, delta: isize) {
    let count = filtered(app).len();
    if count == 0 {
        return;
    }
    app.config_screen.selected = app
        .config_screen
        .selected
        .saturating_add_signed(delta)
        .min(count - 1);
    app.config_screen.free_scroll = false;
}

fn scroll_by(app: &mut App, delta: isize) {
    app.config_screen.scroll = app.config_screen.scroll.saturating_add_signed(delta);
    app.config_screen.free_scroll = true;
}

pub(crate) fn wheel(app: &mut App, delta: isize) {
    scroll_by(app, delta);
}

fn selected(app: &App) -> Option<ConfigField> {
    filtered(app)
        .get(app.config_screen.selected)
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

fn origin_label(origin: &str) -> String {
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
    let runtime_tx = app.model_picker.tx.clone();
    let pending = app.commit_started();
    let op = op.to_owned();
    tokio::spawn(async move {
        let params = serde_json::json!({
            "sessionId": session_id,
            "ops": [{"op": op, "path": field.path, "value": value, "targetLayer": target}],
            "reason": "config screen update",
        });
        let event = match client.request(method::CONFIG_WRITE, params).await {
            Ok(result) => model_picker::Event::Written(result),
            Err(err) => {
                tracing::warn!(%err, path = %field.path, "settings update failed");
                model_picker::Event::Failed
            }
        };
        let applied = matches!(event, model_picker::Event::Written(_));
        crate::input::deliver(runtime_tx, event, &pending).await;
        if applied {
            load(session_id, &client, &tx);
        }
    });
}

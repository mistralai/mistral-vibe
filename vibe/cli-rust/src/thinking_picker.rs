//! `/thinking` picker state. Mirrors Python's `ThinkingPickerApp`: a bottom-panel
//! option list over the model's thinking levels, `›` on the current one, Enter
//! saves and `s` keeps the level for the session. A chained `/model` pick is
//! written with the level in one `config/write`, then one `config/reload`.

use std::sync::Arc;

use serde_json::Value;

use crate::app::{App, ThinkingPicker};
use crate::config_write::{self, Scope};
use crate::server::{method, Client};

/// The canonical five, used until the server sends the model's own set.
pub const THINKING_LEVELS: [&str; 5] = ["off", "low", "medium", "high", "max"];

/// A `/model` pick waiting on its thinking level.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModelPick {
    /// Value written to `/active_model` (`""` for Default).
    pub alias: String,
    /// The model it resolves to, whose `/models/<alias>/thinking` is written.
    pub target: String,
    pub scope: Scope,
}

impl Default for ThinkingPicker {
    fn default() -> Self {
        Self {
            open: false,
            selected: 0,
            scroll: 0,
            current_level: String::new(),
            // Skew-safe default until the server sends the model's set.
            levels: THINKING_LEVELS.iter().map(|it| it.to_string()).collect(),
            model_pick: None,
            tx: None,
        }
    }
}

/// A committed selection's server answer, applied on the main thread.
pub enum Event {
    /// Runtime returned by the follow-up `config/reload`.
    Reloaded(Value),
    /// The write or reload was rejected or failed; show why (Python
    /// `_run_settings_update`).
    Failed(String),
}

/// Apply the server's answer and release the commit that was in flight.
pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Reloaded(runtime) => {
            crate::event_handler::apply_runtime_value(app, &runtime);
            crate::transcript::local::add_status(
                &mut app.view.transcript,
                &crate::commands::submission::new_message_id(),
                crate::commands::event::RELOADED_MESSAGE,
            );
        }
        Event::Failed(error) => {
            show_active_model(app);
            crate::transcript::local::add_command_error(
                &mut app.view.transcript,
                &crate::commands::submission::new_message_id(),
                &error,
            );
        }
    }
    app.commit_finished();
}

/// Open the picker highlighted on the current thinking level.
pub fn open(app: &mut App) {
    if app.thinking_picker.model_pick.take().is_some() {
        show_active_model(app);
    }
    show(app);
}

/// Open on a `/model` pick's own levels before anything is written.
pub fn open_for_model(app: &mut App, pick: ModelPick, current: String, levels: Vec<String>) {
    app.thinking_picker.levels = levels;
    app.thinking_picker.current_level = current;
    app.thinking_picker.model_pick = Some(pick);
    show(app);
}

fn show(app: &mut App) {
    let selected = index_of(
        &app.thinking_picker.levels,
        &app.thinking_picker.current_level,
    );
    app.thinking_picker.selected = selected;
    app.thinking_picker.scroll = 0;
    app.thinking_picker.open = true;
}

/// Where Enter commits: a session-only model pick keeps its level for the session too.
pub fn enter_scope(app: &App) -> Scope {
    match &app.thinking_picker.model_pick {
        Some(pick) => pick.scope,
        None => Scope::Saved,
    }
}

/// Move the highlight up/down by one, wrapping at the ends.
pub fn navigate(app: &mut App, down: bool) {
    let picker = &mut app.thinking_picker;
    picker.selected = crate::list_nav::wrap(picker.selected, picker.levels.len(), down);
}

/// Esc: nothing is written, a chained `/model` pick included.
pub fn cancel(app: &mut App) {
    app.thinking_picker.open = false;
    if app.thinking_picker.model_pick.take().is_some() {
        show_active_model(app);
    }
}

/// Put back the active model's levels a `/model` pick replaced.
fn show_active_model(app: &mut App) {
    let active = &app.model_picker.current_model;
    let Some(model) = app.model_picker.models.iter().find(|m| &m.alias == active) else {
        return;
    };
    app.thinking_picker.levels = model.thinking_levels.clone();
    app.thinking_picker.current_level = model.thinking.clone();
}

/// Commit the highlighted level, with the chained `/model` pick if any.
pub fn select(app: &mut App, client: &Arc<Client>, scope: Scope) {
    let level = selected_level(app).to_owned();
    app.thinking_picker.open = false;
    let pick = app.thinking_picker.model_pick.take();
    let target = match &pick {
        Some(pick) => pick.target.clone(),
        None => app.model_picker.current_model.clone(),
    };
    let mut ops = pick.as_ref().map(model_ops).unwrap_or_default();
    if !target.is_empty() {
        let path = format!("/models/{}/thinking", escape_json_pointer(&target));
        ops.extend(config_write::pick_ops(&path, &level, scope));
    }
    let (reason, prefix) = match &pick {
        Some(pick) => (
            "app-server config update",
            format!("Failed to apply: model {}, thinking {level} — ", pick.alias),
        ),
        None => (
            "app-server thinking update",
            format!("Failed to apply: thinking {level} — "),
        ),
    };
    commit(app, client, ops, reason, prefix);
}

/// Commit a `/model` pick without a thinking level (its model is unresolved).
pub fn commit_model(app: &mut App, client: &Arc<Client>, pick: &ModelPick) {
    let prefix = format!("Failed to apply: model {} — ", pick.alias);
    commit(
        app,
        client,
        model_ops(pick),
        "app-server config update",
        prefix,
    );
}

fn model_ops(pick: &ModelPick) -> Vec<Value> {
    config_write::pick_ops("/active_model", &pick.alias, pick.scope)
}

/// Write `ops`, then `config/reload` (Python `_reload_config`); one event reaches the UI.
fn commit(
    app: &mut App,
    client: &Arc<Client>,
    ops: Vec<Value>,
    reason: &'static str,
    failure_prefix: String,
) {
    if ops.is_empty() {
        return;
    }
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    let thinking_tx = app.thinking_picker.tx.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let written = config_write::write(&client, &session_id, ops, reason).await;
        // Only a clean write is worth reloading; `_run_settings_update` mounts the error.
        let event = match written {
            Err(error) => failed(&failure_prefix, &error),
            Ok(_) => {
                let reload = serde_json::json!({
                    "sessionId": session_id,
                    "reloadRuntime": true,
                });
                match client.request(method::CONFIG_RELOAD, reload).await {
                    Ok(runtime) => Event::Reloaded(runtime),
                    // Python's `_run_settings_update` wraps write + reload
                    // in one try/except: a reload failure mounts the same
                    // "Failed to apply" error as a write rejection.
                    Err(error) => failed(&failure_prefix, &error.to_string()),
                }
            }
        };
        crate::input::deliver(thinking_tx, event, &pending).await;
    });
}

/// Python `_run_settings_update`: a failed update mounts an `ErrorMessage`.
fn failed(prefix: &str, error: &str) -> Event {
    Event::Failed(format!("{prefix}{error}"))
}

/// Escape a JSON pointer token (Python `_escape_json_pointer_token`).
fn escape_json_pointer(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

/// Whether row `i` is the current thinking level.
pub fn is_current(app: &App, i: usize) -> bool {
    app.thinking_picker.levels[i] == app.thinking_picker.current_level
}

/// The level to persist for the highlighted row.
pub fn selected_level(app: &App) -> &str {
    &app.thinking_picker.levels[app.thinking_picker.selected]
}

/// Refresh the picker from a `runtime/read`/`runtime/updated` value (shape
/// `{runtime: {config: {activeModel: {thinking, thinkingLevels}}}}`).
pub fn apply_runtime(app: &mut App, response: &Value) {
    // A pending `/model` pick shows its own model's levels until it is written.
    if app.thinking_picker.model_pick.is_some() {
        return;
    }
    if let Some(level) = response
        .pointer("/runtime/config/activeModel/thinking")
        .and_then(Value::as_str)
    {
        app.thinking_picker.current_level = level.to_owned();
    }
    let levels = response
        .pointer("/runtime/config/activeModel/thinkingLevels")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        });
    // An absent or empty array keeps the previous set (defensive only: the
    // server normalizes [] before it reaches the wire). Only a genuinely new
    // set re-anchors the highlight — runtime pushes arrive for unrelated
    // config changes too, and must not move the user's selection — and keeps
    // `selected` indexing into `levels`.
    if let Some(levels) = levels.filter(|it: &Vec<String>| !it.is_empty()) {
        if levels != app.thinking_picker.levels {
            let current = app.thinking_picker.current_level.clone();
            app.thinking_picker.selected = index_of(&levels, &current);
            app.thinking_picker.levels = levels;
        }
    }
}

fn index_of(levels: &[String], level: &str) -> usize {
    levels.iter().position(|it| it == level).unwrap_or(0)
}

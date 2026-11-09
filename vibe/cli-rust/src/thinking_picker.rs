//! `/thinking` picker state: a bottom-panel option list over the model's
//! thinking levels, `›` on the current one, Enter
//! saves and `s` keeps the level for the session. A chained `/model` pick is
//! written with the level in one write, then one `config/reload`.

use std::sync::Arc;

use serde_json::{json, Value};

use crate::app::{App, ThinkingPicker};
use crate::config_write::{self, Scope};
use crate::server::{method, Client};

/// Shown when a running turn parks a saved pick until it ends.
pub const PARKED_MESSAGE: &str = "Saved; it applies when the current turn ends.";

/// The server's default set, used until it sends the model's own: a server
/// offers whatever levels the model's config lists, as written.
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
            list: Default::default(),
            current_level: String::new(),
            // Skew-safe default until the server sends the model's set.
            levels: THINKING_LEVELS.iter().map(|it| it.to_string()).collect(),
            model_pick: None,
        }
    }
}

/// A committed selection's server answer, applied on the main thread.
pub enum Event {
    /// Runtime returned by the follow-up `config/reload`.
    Reloaded(Value),
    /// A running turn parked the pick; `runtime/updated` announces it once it lands.
    Parked,
    /// The write or reload was rejected or failed; show why.
    Failed(String),
}

/// Apply the server's answer and release the commit that was in flight.
pub fn apply_event(app: &mut App, event: Event) {
    let id = crate::commands::submission::new_message_id();
    match event {
        Event::Reloaded(runtime) => {
            crate::event_handler::apply_runtime_value(app, &runtime);
            let status = crate::commands::event::RELOADED_MESSAGE;
            crate::transcript::local::add_status(&mut app.view.transcript, &id, status);
        }
        Event::Parked => {
            show_active_model(app);
            crate::transcript::local::add_status(&mut app.view.transcript, &id, PARKED_MESSAGE);
        }
        Event::Failed(error) => {
            show_active_model(app);
            crate::transcript::local::add_command_error(&mut app.view.transcript, &id, &error);
        }
    }
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
    app.thinking_picker.list = crate::option_list::OptionList::new(selected);
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
    let len = app.thinking_picker.levels.len();
    app.thinking_picker.list.step(down, len, true, |_| true);
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
    let mut saved = json!({});
    if let Some(pick) = &pick {
        saved["modelAlias"] = json!(pick.alias);
    }
    if !target.is_empty() {
        let path = format!("/models/{}/thinking", escape_json_pointer(&target));
        ops.extend(config_write::pick_ops(&path, &level, scope));
        saved["reasoningEffort"] = json!(level);
    }
    let all_saved = scope == Scope::Saved && pick.as_ref().is_none_or(|p| p.scope == Scope::Saved);
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
    commit(app, client, ops, all_saved.then_some(saved), reason, prefix);
}

/// Commit a `/model` pick without a thinking level (its model is unresolved).
pub fn commit_model(app: &mut App, client: &Arc<Client>, pick: &ModelPick) {
    let prefix = format!("Failed to apply: model {} — ", pick.alias);
    let saved = (pick.scope == Scope::Saved).then(|| json!({"modelAlias": pick.alias}));
    commit(
        app,
        client,
        model_ops(pick),
        saved,
        "app-server config update",
        prefix,
    );
}

fn model_ops(pick: &ModelPick) -> Vec<Value> {
    config_write::pick_ops("/active_model", &pick.alias, pick.scope)
}

/// Write the pick, then `config/reload`; one event reaches the UI. A saved
/// pick (`saved`) goes through `config/model/write`, which a running turn
/// parks; a session-only one stays on the idle-only `config/write` `ops`.
fn commit(
    app: &mut App,
    client: &Arc<Client>,
    ops: Vec<Value>,
    saved: Option<Value>,
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
    crate::ui_event::spawn(app, async move {
        let written =
            crate::model_enforcement::write(&client, &session_id, saved, ops, reason).await;
        // Only a clean, applied write is worth reloading: `config/reload` is
        // idle-only, so a parked pick would read as failed.
        match written {
            Err(error) => failed(&failure_prefix, &error),
            Ok(true) => Event::Parked,
            Ok(false) => {
                let reload = json!({
                    "sessionId": session_id,
                    "reloadRuntime": true,
                });
                match client.request(method::CONFIG_RELOAD, reload).await {
                    Ok(runtime) => Event::Reloaded(runtime),
                    // Write and reload fail as one: a reload failure mounts
                    // the same "Failed to apply" error as a write rejection.
                    Err(error) => failed(
                        &failure_prefix,
                        &crate::server::request_error::describe(&error),
                    ),
                }
            }
        }
    });
}

/// A failed update mounts an error message.
fn failed(prefix: &str, error: &str) -> Event {
    Event::Failed(format!("{prefix}{error}"))
}

/// Escape a JSON pointer token.
fn escape_json_pointer(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

/// Whether row `i` is the current thinking level.
pub fn is_current(app: &App, i: usize) -> bool {
    app.thinking_picker.levels[i] == app.thinking_picker.current_level
}

/// The level to persist for the highlighted row.
pub fn selected_level(app: &App) -> &str {
    &app.thinking_picker.levels[app.thinking_picker.list.selected()]
}

/// Refresh the picker from a `runtime/read`/`runtime/updated` value (shape
/// `{runtime: {config: {activeModel: {thinking, thinkingLevels}}}}`).
pub fn apply_runtime(app: &mut App, response: &Value) {
    // A pending `/model` pick shows its own model's levels until it is written.
    if app.thinking_picker.model_pick.is_some() {
        return;
    }
    let Some(model) = crate::server::RuntimeSnapshot::config(response).and_then(|c| c.active_model)
    else {
        return;
    };
    if let Some(level) = model.thinking {
        app.thinking_picker.current_level = level;
    }
    let levels = model.thinking_levels;
    // An absent or empty array keeps the previous set (defensive only: the
    // server normalizes [] before it reaches the wire). Only a genuinely new
    // set re-anchors the highlight — runtime pushes arrive for unrelated
    // config changes too, and must not move the user's selection — and keeps
    // `selected` indexing into `levels`.
    if !levels.is_empty() && levels != app.thinking_picker.levels {
        let current = &app.thinking_picker.current_level;
        let index = index_of(&levels, current);
        app.thinking_picker.list.highlight(index);
        app.thinking_picker.levels = levels;
    }
}

fn index_of(levels: &[String], level: &str) -> usize {
    levels.iter().position(|it| it == level).unwrap_or(0)
}

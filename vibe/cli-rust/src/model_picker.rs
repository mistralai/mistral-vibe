//! `/model` picker state. Mirrors Python's `ModelPickerApp`: a leading Default
//! (unpinned) row plus one row per configured model, current row marked, no live
//! preview; the selection persists on Enter.

use serde_json::Value;

use crate::app::{App, ModelOption};
use crate::commands::event::RELOADED_MESSAGE;
use crate::commands::submission::new_message_id;
use crate::event_handler;
use crate::transcript::local;

/// Alias persisted for the Default (unpinned) row (Python `UNPINNED_ACTIVE_MODEL`).
pub const UNPINNED_ACTIVE_MODEL: &str = "";

/// A committed selection's server answer, applied on the main thread.
pub enum Event {
    /// Runtime returned by `config/write` when the follow-up reload failed.
    Written(Value),
    /// Runtime returned by the follow-up `config/reload` (Python `_reload_config`).
    Reloaded(Value),
    /// The write failed; nothing to apply.
    Failed,
}

/// Apply the server's answer and release the commit that was in flight.
pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Written(runtime) => event_handler::apply_runtime_value(app, &runtime),
        Event::Reloaded(runtime) => {
            event_handler::apply_runtime_value(app, &runtime);
            local::add_status(
                &mut app.view.transcript,
                &new_message_id(),
                RELOADED_MESSAGE,
            );
        }
        Event::Failed => {}
    }
    app.commit_finished();
}

/// Open the picker highlighted on the current choice (Python `on_mount`).
pub fn open(app: &mut App) {
    app.model_picker.selected = current_index(app);
    app.model_picker.scroll = 0;
    app.model_picker.free_scroll = false;
    app.model_picker.open = true;
}

/// Pre-selected row: pinned model `+1` (offset by Default), else `0` (Default).
fn current_index(app: &App) -> usize {
    if app.model_picker.is_pinned {
        if let Some(i) = model_index(app) {
            return i + 1;
        }
    }
    0
}

/// Index into `models` of the currently active alias, if any.
fn model_index(app: &App) -> Option<usize> {
    app.model_picker
        .models
        .iter()
        .position(|m| m.alias == app.model_picker.current_model)
}

/// Row count: the Default row plus one per model.
pub fn option_count(app: &App) -> usize {
    app.model_picker.models.len() + 1
}

/// Move the highlight up/down by one, clamped (Textual `OptionList` no wrap).
pub fn navigate(app: &mut App, down: bool) {
    app.model_picker.free_scroll = false;
    let last = option_count(app) - 1;
    if down {
        app.model_picker.selected = (app.model_picker.selected + 1).min(last);
    } else {
        app.model_picker.selected = app.model_picker.selected.saturating_sub(1);
    }
}

/// Whether row `i` is the current choice: Default when unpinned, else the pinned model.
pub fn is_current(app: &App, i: usize) -> bool {
    if i == 0 {
        return !app.model_picker.is_pinned;
    }
    app.model_picker.is_pinned && model_index(app) == Some(i - 1)
}

/// The alias to persist for the highlighted row (`""` for Default).
pub fn selected_alias(app: &App) -> String {
    let sel = app.model_picker.selected;
    if sel == 0 {
        return UNPINNED_ACTIVE_MODEL.to_owned();
    }
    app.model_picker
        .models
        .get(sel - 1)
        .map(|m| m.alias.clone())
        .unwrap_or_default()
}

/// Close the picker without persisting (Esc / cancel).
pub fn cancel(app: &mut App) {
    app.model_picker.open = false;
}

/// Refresh the model snapshot from a `runtime/read`/`runtime/updated` value
/// (shape `{runtime: {config: {...}}}`), so the picker reflects live config.
pub fn apply_runtime(app: &mut App, response: &Value) {
    let Some(config) = response.pointer("/runtime/config") else {
        return;
    };
    app.model_picker.models = config
        .get("models")
        .and_then(Value::as_array)
        .map(|models| models.iter().filter_map(read_model).collect())
        .unwrap_or_default();
    app.model_picker.current_model = config
        .pointer("/activeModel/alias")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    app.model_picker.is_pinned = config
        .get("activeModelPinned")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    app.model_picker.default_display_name = default_display_name(config);
}

/// Display name of the default model, looked up by `defaultModelAlias` (Python
/// `ConfigView.default_display_name`); falls back to the alias itself.
fn default_display_name(config: &Value) -> String {
    let alias = config
        .get("defaultModelAlias")
        .and_then(Value::as_str)
        .unwrap_or_default();
    config
        .get("models")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|m| m.get("alias").and_then(Value::as_str) == Some(alias))
        .and_then(|m| m.get("displayName").and_then(Value::as_str))
        .unwrap_or(alias)
        .to_owned()
}

fn read_model(value: &Value) -> Option<ModelOption> {
    let alias = value.get("alias").and_then(Value::as_str)?.to_owned();
    let display_name = value
        .get("displayName")
        .and_then(Value::as_str)
        .unwrap_or(&alias)
        .to_owned();
    Some(ModelOption {
        alias,
        display_name,
    })
}

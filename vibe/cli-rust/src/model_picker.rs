//! `/model` picker state. Mirrors Python's `ModelPickerApp`: a leading Default
//! (unpinned) row plus one row per configured model, current row marked, no live
//! preview. A pick chains straight into `/thinking`, which writes both at once.

use std::sync::Arc;

use serde_json::Value;

use crate::app::{App, ModelOption};
use crate::config_write::Scope;
use crate::event_handler;
use crate::server::Client;
use crate::thinking_picker::{self, ModelPick, THINKING_LEVELS};

/// Alias persisted for the Default (unpinned) row (Python `UNPINNED_ACTIVE_MODEL`).
pub const UNPINNED_ACTIVE_MODEL: &str = "";

/// A `/config` model write's server answer, applied on the main thread.
pub enum Event {
    /// Runtime returned by `config/write`.
    Written(Value),
    /// The write failed; nothing to apply.
    Failed,
}

/// Apply the server's answer and release the commit that was in flight.
pub fn apply_event(app: &mut App, event: Event) {
    if let Event::Written(runtime) = event {
        event_handler::apply_runtime_value(app, &runtime);
    }
    app.commit_finished();
}

/// Hand the highlighted model to the thinking picker, opened at once on its
/// levels; nothing is written until that picker commits or is dismissed. A
/// model that cannot be resolved has no levels to offer and is written alone.
pub fn select(app: &mut App, client: &Arc<Client>, scope: Scope) {
    let alias = selected_alias(app);
    app.model_picker.open = false;
    let target = match alias.as_str() {
        UNPINNED_ACTIVE_MODEL => app.model_picker.default_alias.clone(),
        _ => alias.clone(),
    };
    let option = app.model_picker.models.iter().find(|m| m.alias == target);
    let Some((thinking, levels)) = option.map(|m| (m.thinking.clone(), m.thinking_levels.clone()))
    else {
        let pick = ModelPick {
            alias,
            target: String::new(),
            scope,
        };
        thinking_picker::commit_model(app, client, &pick);
        return;
    };
    let pick = ModelPick {
        alias,
        target,
        scope,
    };
    thinking_picker::open_for_model(app, pick, thinking, levels);
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

/// Move the highlight up/down by one, wrapping at the ends.
pub fn navigate(app: &mut App, down: bool) {
    app.model_picker.free_scroll = false;
    let count = option_count(app);
    app.model_picker.selected = crate::list_nav::wrap(app.model_picker.selected, count, down);
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
    app.model_picker.default_alias = config
        .get("defaultModelAlias")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
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
    let thinking = value
        .get("thinking")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    // Older servers omit the per-model set; fall back to the canonical five (ADR 0014).
    let thinking_levels = value
        .get("thinkingLevels")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .filter(|levels: &Vec<String>| !levels.is_empty())
        .unwrap_or_else(|| THINKING_LEVELS.iter().map(|it| it.to_string()).collect());
    Some(ModelOption {
        alias,
        display_name,
        thinking,
        thinking_levels,
    })
}

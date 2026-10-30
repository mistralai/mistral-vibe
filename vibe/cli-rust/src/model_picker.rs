//! `/model` picker state: a leading Default
//! (unpinned) row plus one row per configured model, current row marked, no live
//! preview. A pick chains straight into `/thinking`, which writes both at once.

use std::sync::Arc;

use serde_json::Value;

use crate::app::{App, ModelOption, ToastSeverity};
use crate::config_write::Scope;
use crate::event_handler;
use crate::server::Client;
use crate::thinking_picker::{self, ModelPick, THINKING_LEVELS};

/// Alias persisted for the Default (unpinned) row.
pub const UNPINNED_ACTIVE_MODEL: &str = "";

/// A `/config` field write's server answer, applied on the main thread.
pub enum Event {
    /// Runtime returned by `config/write`.
    Written(Value),
    /// The write failed; the message names the field and the server's reason.
    Failed(String),
}

pub(crate) const FAILED_TOAST_SECS: u64 = 8;

/// Apply the server's answer and release the commit that was in flight.
pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Written(runtime) => event_handler::apply_runtime_value(app, &runtime),
        Event::Failed(message) => app.show_toast(message, ToastSeverity::Error, FAILED_TOAST_SECS),
    }
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

/// Open the picker highlighted on the current choice.
pub fn open(app: &mut App) {
    app.model_picker.list = crate::option_list::OptionList::new(current_index(app));
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
    let count = option_count(app);
    app.model_picker.list.step(down, count, true, |_| true);
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
    let sel = app.model_picker.list.selected();
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
    let Some(config) = crate::server::RuntimeSnapshot::config(response) else {
        return;
    };
    let models: Vec<ModelOption> = config.models.into_iter().filter_map(model_option).collect();
    let alias = config.default_model_alias;
    app.model_picker.default_display_name = models
        .iter()
        .find(|model| model.alias == alias)
        .map_or_else(|| alias.clone(), |model| model.display_name.clone());
    app.model_picker.models = models;
    app.model_picker.current_model = config.active_model.map(|m| m.alias).unwrap_or_default();
    app.model_picker.is_pinned = config.active_model_pinned;
    app.model_picker.default_alias = alias;
}

fn model_option(model: crate::server::ModelView) -> Option<ModelOption> {
    if model.alias.is_empty() {
        return None;
    }
    // Older servers omit the per-model set; fall back to the canonical five (ADR 0014).
    let thinking_levels = match model.thinking_levels.is_empty() {
        true => THINKING_LEVELS.iter().map(|it| it.to_string()).collect(),
        false => model.thinking_levels,
    };
    Some(ModelOption {
        display_name: model.display_name.unwrap_or_else(|| model.alias.clone()),
        alias: model.alias,
        thinking: model.thinking.unwrap_or_default(),
        thinking_levels,
    })
}

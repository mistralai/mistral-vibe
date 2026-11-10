//! `vibe.model_selection.*` events of the `/model` and `/thinking` pickers, shaped as Vibe Code Web's.

use std::collections::BTreeMap;

use serde_json::{json, Value};

use crate::app::App;
use crate::model_picker::UNPINNED_ACTIVE_MODEL;
use crate::telemetry::{self, bmap, event};

/// The picker an `opened` event names.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Picker {
    Model,
    ReasoningEffort,
}

impl Picker {
    fn name(self) -> &'static str {
        match self {
            Picker::Model => "model",
            Picker::ReasoningEffort => "reasoning_effort",
        }
    }
}

/// Record that `picker` opened, on the choices it offers.
pub fn opened(app: &App, picker: Picker) {
    let properties = with_base(json!({
        "picker": picker.name(),
        "has_model_choice": !app.model_picker.models.is_empty(),
        "has_reasoning_effort_choice": app.thinking_picker.levels.len() > 1,
    }));
    telemetry::record(app, event::MODEL_SELECTION_OPENED, properties);
}

/// A write's `selected` events: the model pick (`""` is Default), then the level, which a `/model`
/// chain sends only when it differs from the preselected one.
pub fn picks(
    app: &App,
    model_alias: Option<&str>,
    level: Option<&str>,
) -> Vec<BTreeMap<String, Value>> {
    let picker = &app.model_picker;
    let active = event_model(match picker.is_pinned {
        true => &picker.current_model,
        false => UNPINNED_ACTIVE_MODEL,
    });
    let preselected = app.thinking_picker.current_level.as_str();
    let model = model_alias.map_or_else(|| active.clone(), event_model);
    let mut selections = Vec::new();
    if model_alias.is_some() {
        selections.push(with_base(json!({
            "selection_type": "model",
            "model": model,
            "previous_model": active,
            "default_model": picker.default_alias,
            "is_default_model": model == event_model("") || model == picker.default_alias,
        })));
    }
    if let Some(level) = level.filter(|level| model_alias.is_none() || *level != preselected) {
        selections.push(with_base(json!({
            "selection_type": "reasoning_effort",
            "model": model,
            "reasoning_effort": level,
            "previous_reasoning_effort": preselected,
        })));
    }
    selections
}

/// An alias as the events name it: Vibe Code Web calls the unpinned option `default`.
fn event_model(alias: &str) -> String {
    match alias {
        UNPINNED_ACTIVE_MODEL => "default".to_owned(),
        alias => alias.to_owned(),
    }
}

fn with_base(properties: Value) -> BTreeMap<String, Value> {
    let mut map = bmap(json!({"source": "session", "host_kind": "local"}));
    map.extend(bmap(properties));
    map
}

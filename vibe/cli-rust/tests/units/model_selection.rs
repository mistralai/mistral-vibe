//! `vibe.model_selection.*` cases the client-e2e goldens do not reach.

use std::collections::BTreeMap;

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::model_picker;
use vibe_rs::model_selection;
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::thinking_picker;

/// On `medium`, pinned or on Default; `devstral` saves `medium`.
fn app(pinned: Option<&str>) -> App {
    let mut app = App::default();
    let runtime = json!({"runtime": {"config": {
        "activeModel": {"alias": pinned.unwrap_or("medium"), "thinking": "high", "thinkingLevels": ["low", "medium", "high"]},
        "activeModelPinned": pinned.is_some(),
        "defaultModelAlias": "medium",
        "models": [
            {"alias": "medium", "thinking": "high", "thinkingLevels": ["low", "medium", "high"]},
            {"alias": "devstral", "thinking": "medium", "thinkingLevels": ["low", "medium"]}
        ]
    }}});
    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime);
    app
}

fn field<'a>(props: &'a BTreeMap<String, Value>, key: &str) -> &'a Value {
    props.get(key).unwrap_or(&Value::Null)
}

#[test]
fn each_picker_records_one_opened_naming_it() {
    let mut app = app(None);
    let (tx, mut rx) = tokio::sync::mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);
    model_picker::open(&mut app);
    thinking_picker::open(&mut app);
    let events: Vec<TelemetryEvent> = std::iter::from_fn(|| rx.try_recv().ok()).collect();
    let names: Vec<_> = events.iter().map(|event| event.name).collect();
    assert_eq!(names, [event::MODEL_SELECTION_OPENED; 2]);
    assert_eq!(field(&events[0].properties, "picker"), "model");
    assert_eq!(field(&events[1].properties, "picker"), "reasoning_effort");
    assert_eq!(
        field(&events[0].properties, "has_reasoning_effort_choice"),
        true
    );
}

#[test]
fn a_model_other_than_the_default_is_not_a_default_pick() {
    let mut app = app(None);
    model_picker::open(&mut app);
    app.model_picker.list.highlight(2);
    model_picker::select(
        &mut app,
        &crate::support::stub(),
        vibe_rs::config_write::Scope::Saved,
    );
    let picks = model_selection::picks(&app, Some("devstral"), Some("medium"));
    assert_eq!(picks.len(), 1);
    assert_eq!(field(&picks[0], "model"), "devstral");
    assert_eq!(field(&picks[0], "previous_model"), "default");
    assert_eq!(field(&picks[0], "default_model"), "medium");
    assert_eq!(field(&picks[0], "is_default_model"), false);
}

#[test]
fn picking_the_active_model_again_still_records_it() {
    let app = app(Some("devstral"));
    let pick = &model_selection::picks(&app, Some("devstral"), None)[0];
    assert_eq!(field(pick, "model"), "devstral");
    assert_eq!(field(pick, "previous_model"), "devstral");
}

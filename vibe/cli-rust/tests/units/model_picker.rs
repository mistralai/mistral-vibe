//! A model pick opens the thinking picker at once on the picked model's levels, holding the pick.

use std::sync::Arc;

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::config_write::Scope;
use vibe_rs::model_picker;
use vibe_rs::server::Client;
use vibe_rs::thinking_picker::{self, ModelPick};

fn runtime() -> Value {
    json!({"runtime": {"config": {
        "activeModel": {"alias": "medium", "thinking": "off", "thinkingLevels": ["off", "low"]},
        "defaultModelAlias": "medium",
        "models": [
            {"alias": "medium", "thinking": "off", "thinkingLevels": ["off", "low"]},
            {"alias": "large", "thinking": "high", "thinkingLevels": ["off", "high", "max"]},
            {"alias": "legacy", "thinking": "low"}
        ]
    }}})
}

fn select(app: &mut App, scope: Scope) {
    model_picker::select(app, &Arc::new(Client::stub()), scope);
}

fn app_on(row: usize) -> App {
    let mut app = App::default();
    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime());
    model_picker::open(&mut app);
    app.model_picker.selected = row;
    app
}

#[test]
fn pick_opens_thinking_on_the_picked_model() {
    let mut app = app_on(2);
    select(&mut app, Scope::Session);
    assert!(!app.model_picker.open);
    assert!(app.thinking_picker.open);
    assert_eq!(app.thinking_picker.levels, vec!["off", "high", "max"]);
    assert_eq!(thinking_picker::selected_level(&app), "high");
    assert_eq!(thinking_picker::enter_scope(&app), Scope::Session);
    assert_eq!(
        app.thinking_picker.model_pick,
        Some(ModelPick {
            alias: "large".into(),
            target: "large".into(),
            scope: Scope::Session,
        })
    );
}

#[test]
fn default_row_targets_the_default_model() {
    let mut app = app_on(0);
    select(&mut app, Scope::Saved);
    let pick = app.thinking_picker.model_pick.clone().unwrap();
    assert_eq!((pick.alias.as_str(), pick.target.as_str()), ("", "medium"));
    assert_eq!(app.thinking_picker.levels, vec!["off", "low"]);
}

#[test]
fn model_without_levels_falls_back_to_the_canonical_five() {
    let mut app = app_on(3);
    select(&mut app, Scope::Saved);
    assert_eq!(app.thinking_picker.levels, thinking_picker::THINKING_LEVELS);
    assert_eq!(thinking_picker::selected_level(&app), "low");
}

#[test]
fn runtime_push_does_not_override_a_pending_pick() {
    let mut app = app_on(2);
    select(&mut app, Scope::Saved);
    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime());
    assert_eq!(app.thinking_picker.levels, vec!["off", "high", "max"]);
}

#[test]
fn plain_thinking_open_drops_a_stale_pick() {
    let mut app = app_on(2);
    select(&mut app, Scope::Session);
    thinking_picker::open(&mut app);
    assert_eq!(app.thinking_picker.model_pick, None);
    assert_eq!(thinking_picker::enter_scope(&app), Scope::Saved);
    assert_eq!(app.thinking_picker.levels, vec!["off", "low"]);
    assert_eq!(thinking_picker::selected_level(&app), "off");
}

#[test]
fn esc_drops_the_pick_and_restores_the_active_model() {
    let mut app = app_on(2);
    select(&mut app, Scope::Saved);
    thinking_picker::cancel(&mut app);
    assert!(!app.thinking_picker.open);
    assert_eq!(app.thinking_picker.model_pick, None);
    assert_eq!(app.thinking_picker.levels, vec!["off", "low"]);
    assert_eq!(app.thinking_picker.current_level, "off");
}

#[test]
fn unresolved_default_skips_the_thinking_picker() {
    let mut runtime = runtime();
    runtime["runtime"]["config"]
        .as_object_mut()
        .unwrap()
        .remove("defaultModelAlias");
    let mut app = App::default();
    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime);
    model_picker::open(&mut app);
    app.model_picker.selected = 0;
    select(&mut app, Scope::Saved);
    assert!(!app.model_picker.open);
    assert!(!app.thinking_picker.open);
    assert_eq!(app.thinking_picker.model_pick, None);
}

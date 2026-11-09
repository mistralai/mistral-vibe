//! A model pick opens the thinking picker at once on the picked model's levels, holding the pick.

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::config_write::Scope;
use vibe_rs::model_picker::{self, Row};
use vibe_rs::thinking_picker::{self, ModelPick};

use crate::support::{click, key, redraw, row, stub};

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
    model_picker::select(app, &stub(), scope);
}

fn app_on(row: usize) -> App {
    let mut app = App::default();
    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime());
    model_picker::open(&mut app);
    app.model_picker.list.highlight(row);
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
    app.model_picker.list.highlight(0);
    select(&mut app, Scope::Saved);
    assert!(!app.model_picker.open);
    assert!(!app.thinking_picker.open);
    assert_eq!(app.thinking_picker.model_pick, None);
}

#[test]
fn a_pinned_model_opens_highlighted_and_marked_current() {
    let mut runtime = runtime();
    runtime["runtime"]["config"]["activeModel"]["alias"] = json!("large");
    runtime["runtime"]["config"]["activeModelPinned"] = json!(true);
    let mut app = App::default();
    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime);

    model_picker::open(&mut app);

    assert_eq!(app.model_picker.list.selected(), 2);
    assert!(model_picker::is_current(&app, 2));
    assert!(!model_picker::is_current(&app, 0));
    assert_eq!(model_picker::selected_alias(&app), "large");
}

fn type_query(app: &mut App, text: &str) {
    assert!(model_picker::handle_search_key(
        app,
        &key(KeyCode::Char('/'))
    ));
    for ch in text.chars() {
        assert!(model_picker::handle_search_key(
            app,
            &key(KeyCode::Char(ch))
        ));
    }
}

#[test]
fn search_filters_rows_and_the_highlight_follows() {
    let mut app = app_on(0);
    type_query(&mut app, "large");
    assert_eq!(app.model_picker.search.query, "large");
    assert_eq!(model_picker::rows(&app), vec![Row::Model(1)]);
    assert_eq!(model_picker::option_count(&app), 1);
    assert_eq!(app.model_picker.list.selected(), 0);
    assert_eq!(model_picker::selected_alias(&app), "large");
    assert!(!model_picker::is_current(&app, 0));
}

#[test]
fn search_matches_the_alias_and_the_default_row() {
    let mut app = app_on(0);
    type_query(&mut app, "def");
    assert_eq!(model_picker::rows(&app), vec![Row::Default]);
    assert_eq!(
        model_picker::selected_alias(&app),
        model_picker::UNPINNED_ACTIVE_MODEL
    );
}

#[test]
fn no_match_shows_a_note_row_and_select_does_nothing() {
    let mut app = app_on(0);
    type_query(&mut app, "zzz");
    assert_eq!(model_picker::rows(&app), vec![Row::Note]);
    model_picker::navigate(&mut app, true);
    assert_eq!(
        app.model_picker.list.selected(),
        0,
        "the note cannot take the highlight"
    );
    select(&mut app, Scope::Saved);
    assert!(app.model_picker.open);
    assert!(!app.thinking_picker.open);
    assert_eq!(app.thinking_picker.model_pick, None);
}

#[test]
fn navigation_wraps_within_the_filtered_rows() {
    let mut app = app_on(0);
    type_query(&mut app, "l");
    let count = model_picker::option_count(&app);
    assert_eq!(count, 3, "Default, large and legacy all match `l`");
    for _ in 0..count {
        model_picker::navigate(&mut app, true);
    }
    assert_eq!(app.model_picker.list.selected(), 0);
    model_picker::navigate(&mut app, false);
    assert_eq!(app.model_picker.list.selected(), count - 1);
}

#[test]
fn esc_leaves_search_then_clears_the_filter_then_passes() {
    let mut app = app_on(0);
    type_query(&mut app, "large");
    assert!(model_picker::handle_search_key(
        &mut app,
        &key(KeyCode::Esc)
    ));
    assert!(!app.model_picker.search.focused);
    assert_eq!(
        app.model_picker.search.query, "large",
        "the filter stays applied"
    );
    assert!(model_picker::handle_search_key(
        &mut app,
        &key(KeyCode::Esc)
    ));
    assert_eq!(app.model_picker.search.query, "");
    assert_eq!(model_picker::option_count(&app), 4, "the full list is back");
    assert!(
        !model_picker::handle_search_key(&mut app, &key(KeyCode::Esc)),
        "then Esc reaches cancel"
    );
}

#[test]
fn list_keys_pass_through_while_the_search_field_is_focused() {
    let mut app = app_on(0);
    type_query(&mut app, "l");
    for code in [KeyCode::Up, KeyCode::Down, KeyCode::Enter] {
        assert!(
            !model_picker::handle_search_key(&mut app, &key(code)),
            "{code:?}"
        );
    }
    assert!(app.model_picker.search.focused);
}

#[test]
fn paste_filters_only_while_the_field_is_focused() {
    let mut app = app_on(0);
    model_picker::paste_search(&mut app, "large");
    assert_eq!(app.model_picker.search.query, "");
    model_picker::focus_search(&mut app);
    model_picker::paste_search(&mut app, "large");
    assert_eq!(app.model_picker.search.query, "large");
    assert_eq!(model_picker::rows(&app), vec![Row::Model(1)]);
}

#[test]
fn opening_the_picker_resets_a_stale_filter() {
    let mut app = app_on(0);
    type_query(&mut app, "large");
    model_picker::open(&mut app);
    assert_eq!(app.model_picker.search.query, "");
    assert_eq!(model_picker::option_count(&app), 4);
}

#[test]
fn the_search_row_sits_under_the_title_and_a_click_focuses_it() {
    let mut app = app_on(0);
    let buffer = redraw(&mut app);
    let title = (0..buffer.area.height)
        .find(|y| row(&buffer, *y).contains("Select Model"))
        .expect("the title");
    let search = (0..buffer.area.height)
        .find(|y| row(&buffer, *y).contains("Search models"))
        .expect("the search placeholder");
    assert_eq!(
        search,
        title + 1,
        "the search row sits directly under the title"
    );
    assert!(
        row(&buffer, search + 1).trim_matches(['│', ' ']).is_empty(),
        "one blank row follows the search"
    );
    assert_eq!(app.model_picker.search.area.y, search);

    let field = app.model_picker.search.area;
    click(&mut app, field.x + 1, field.y);
    assert!(app.model_picker.search.focused);

    model_picker::navigate(&mut app, true);
    click(&mut app, 4, field.y + 4);
    assert!(
        !app.model_picker.search.focused,
        "a click on a row leaves the field"
    );
    assert!(
        app.model_picker.open,
        "a click off the highlight only highlights"
    );
}

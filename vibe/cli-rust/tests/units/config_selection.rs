//! Config refreshes preserve the selected field and browser position.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::{config, config_fields};

use crate::feedback_support::errors;
use crate::support::{redraw, rows};

fn loaded(names: &[&str]) -> config::Loaded {
    config_fields::parse(&json!({
        "fields": names.iter().map(|name| json!({
            "name": name, "path": format!("/{name}"), "kind": "int", "value": 42,
        })).collect::<Vec<_>>(),
        "targets": ["user-toml", "overrides"],
    }))
}

#[test]
fn refresh_preserves_selection_filter_and_scroll() {
    let mut app = App::default();
    config::apply_loaded(&mut app, loaded(&["other", "setting_a", "setting_b"]));
    app.config_screen.search.query = "setting".into();
    app.config_screen.list.highlight(1);
    app.config_screen.list.detach_at(12);
    app.config_screen.loading = true;

    config::apply_loaded(&mut app, loaded(&["other", "setting_a", "setting_b"]));

    assert_eq!(app.config_screen.list.selected(), 1);
    assert_eq!(app.config_screen.search.query, "setting");
    assert_eq!(app.config_screen.list.scroll(), 12);
    assert!(app.config_screen.list.free_scroll());
    assert!(!app.config_screen.loading);
    assert_eq!(app.config_screen.targets, ["user-toml", "overrides"]);
}

#[test]
fn refresh_tracks_the_field_when_rows_move() {
    let mut app = App::default();
    config::apply_loaded(&mut app, loaded(&["a", "b", "c"]));
    app.config_screen.list.highlight(1);

    config::apply_loaded(&mut app, loaded(&["c", "a", "b"]));

    assert_eq!(app.config_screen.list.selected(), 2);
    assert_eq!(config::filtered(&app)[2].path, "/b");
}

#[test]
fn removed_selection_clamps_to_a_remaining_row() {
    let mut app = App::default();
    config::apply_loaded(&mut app, loaded(&["a", "b", "c"]));
    app.config_screen.list.highlight(2);

    config::apply_loaded(&mut app, loaded(&["a", "b"]));

    assert_eq!(app.config_screen.list.selected(), 1);
}

#[test]
fn empty_refresh_has_a_safe_selection() {
    let mut app = App::default();
    config::apply_loaded(&mut app, loaded(&["a", "b"]));
    app.config_screen.list.highlight(1);

    config::apply_loaded(&mut app, loaded(&[]));

    assert_eq!(app.config_screen.list.selected(), 0);
    assert!(config::filtered(&app).is_empty());
}

fn failed() -> config::Loaded {
    config::Loaded {
        error: Some("app-server closed".into()),
        ..config::Loaded::default()
    }
}

#[test]
fn failed_refresh_keeps_the_rows_and_selection() {
    for failure in [failed(), config_fields::parse(&json!({}))] {
        let mut app = App::default();
        app.config_screen.open = true;
        config::apply_loaded(&mut app, loaded(&["a", "b"]));
        app.config_screen.list.highlight(1);
        app.config_screen.loading = true;

        config::apply_loaded(&mut app, failure);

        assert!(!app.config_screen.loading);
        assert_eq!(app.config_screen.list.selected(), 1);
        assert_eq!(config::filtered(&app).len(), 2);
        assert_eq!(errors(&app).len(), 1);
        assert!(app
            .config_screen
            .status
            .as_ref()
            .is_some_and(|status| status.text.starts_with("Failed to load settings: ")));
    }
}

#[test]
fn failed_refresh_after_close_reports_nothing() {
    let mut app = App::default();
    config::apply_loaded(&mut app, failed());
    assert!(errors(&app).is_empty());
    assert!(app.config_screen.status.is_none());
}

#[test]
fn a_failed_refresh_replaces_the_hints() {
    let mut app = App::default();
    app.config_screen.open = true;
    config::apply_loaded(&mut app, loaded(&["a"]));
    config::apply_loaded(&mut app, failed());

    let screen = rows(&redraw(&mut app)).join("\n");
    assert!(screen.contains("Failed to load settings: app-server closed"));
    assert!(app.config_screen.status.as_ref().unwrap().shown());
}

#[test]
fn a_refusal_from_behind_config_shows_on_its_status_line() {
    let mut app = App::default();
    app.config_screen.open = true;

    vibe_rs::notices::failed(&mut app, "Failed to switch agent: broken");

    assert_eq!(errors(&app), ["Failed to switch agent: broken"]);
    assert!(app
        .config_screen
        .status
        .as_ref()
        .is_some_and(|status| status.text == "Failed to switch agent: broken"));
}

#[test]
fn a_status_waits_for_a_screen_large_enough_to_paint_it() {
    let mut app = App::default();
    app.config_screen.open = true;
    vibe_rs::notices::failed(&mut app, "Failed to switch agent: broken");

    crate::support::render(18, 6, |frame| app.draw(frame));
    assert!(
        !app.config_screen.status.as_ref().unwrap().shown(),
        "too small to paint it"
    );

    redraw(&mut app);
    assert!(app.config_screen.status.as_ref().unwrap().shown());
}

#[test]
fn a_status_under_the_edit_dialog_waits_for_it_to_close() {
    let mut app = App::default();
    app.config_screen.open = true;
    config::apply_loaded(&mut app, loaded(&["a"]));
    let field = config::filtered(&app)[0].clone();
    vibe_rs::config_edit::open(&mut app, field);
    vibe_rs::notices::failed(&mut app, "Failed to save a (temporary): rejected");

    redraw(&mut app);
    assert!(
        !app.config_screen.status.as_ref().unwrap().shown(),
        "covered by the dialog"
    );

    app.config_screen.edit = None;
    redraw(&mut app);
    assert!(app.config_screen.status.as_ref().unwrap().shown());
}

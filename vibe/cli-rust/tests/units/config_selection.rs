//! Config refreshes preserve the selected field and browser position.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::{config, config_fields};

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
        assert_eq!(app.overlays.toasts.len(), 1);
    }
}

#[test]
fn failed_refresh_after_close_shows_no_toast() {
    let mut app = App::default();
    config::apply_loaded(&mut app, failed());
    assert!(app.overlays.toasts.is_empty());
}

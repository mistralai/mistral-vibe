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
    app.config_screen.query = "setting".into();
    app.config_screen.selected = 1;
    app.config_screen.scroll = 12;
    app.config_screen.free_scroll = true;
    app.config_screen.loading = true;

    config::apply_loaded(&mut app, loaded(&["other", "setting_a", "setting_b"]));

    assert_eq!(app.config_screen.selected, 1);
    assert_eq!(app.config_screen.query, "setting");
    assert_eq!(app.config_screen.scroll, 12);
    assert!(app.config_screen.free_scroll);
    assert!(!app.config_screen.loading);
    assert_eq!(app.config_screen.targets, ["user-toml", "overrides"]);
}

#[test]
fn refresh_tracks_the_field_when_rows_move() {
    let mut app = App::default();
    config::apply_loaded(&mut app, loaded(&["a", "b", "c"]));
    app.config_screen.selected = 1;

    config::apply_loaded(&mut app, loaded(&["c", "a", "b"]));

    assert_eq!(app.config_screen.selected, 2);
    assert_eq!(config::filtered(&app)[2].path, "/b");
}

#[test]
fn removed_selection_clamps_to_a_remaining_row() {
    let mut app = App::default();
    config::apply_loaded(&mut app, loaded(&["a", "b", "c"]));
    app.config_screen.selected = 2;

    config::apply_loaded(&mut app, loaded(&["a", "b"]));

    assert_eq!(app.config_screen.selected, 1);
}

#[test]
fn empty_refresh_has_a_safe_selection() {
    let mut app = App::default();
    config::apply_loaded(&mut app, loaded(&["a", "b"]));
    app.config_screen.selected = 1;

    config::apply_loaded(&mut app, loaded(&[]));

    assert_eq!(app.config_screen.selected, 0);
    assert!(config::filtered(&app).is_empty());
}

//! The shared list search model: `/` focuses, Esc leaves then clears, list keys pass through.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::search_field::{self, Outcome, Search};
use vibe_rs::server::Client;
use vibe_rs::{config, config_fields, plugins};

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn press(search: &mut Search, code: KeyCode) -> Outcome {
    search_field::handle_key(search, &key(code))
}

#[test]
fn slash_focuses_and_typing_filters_including_j_and_k() {
    let mut search = Search::default();
    assert_eq!(press(&mut search, KeyCode::Char('j')), Outcome::Pass);
    assert_eq!(press(&mut search, KeyCode::Char('/')), Outcome::Consumed);
    assert!(search.focused);
    for ch in "jk".chars() {
        assert_eq!(press(&mut search, KeyCode::Char(ch)), Outcome::Filtered);
    }
    assert_eq!(search.query, "jk");
}

#[test]
fn list_keys_pass_through_while_the_field_is_focused() {
    let mut search = Search {
        focused: true,
        ..Search::default()
    };
    for code in [
        KeyCode::Up,
        KeyCode::Down,
        KeyCode::PageUp,
        KeyCode::PageDown,
        KeyCode::Enter,
    ] {
        assert_eq!(press(&mut search, code), Outcome::Pass, "{code:?}");
        assert!(search.focused);
    }
}

#[test]
fn escape_leaves_the_field_then_clears_the_filter_then_passes() {
    let mut search = Search::default();
    press(&mut search, KeyCode::Char('/'));
    press(&mut search, KeyCode::Char('x'));
    assert_eq!(press(&mut search, KeyCode::Esc), Outcome::Consumed);
    assert!(!search.focused);
    assert_eq!(search.query, "x", "the filter stays applied");
    assert_eq!(press(&mut search, KeyCode::Esc), Outcome::Filtered);
    assert_eq!(search.query, "");
    assert_eq!(press(&mut search, KeyCode::Esc), Outcome::Pass);
}

#[test]
fn hints_follow_the_search_state() {
    let list = [
        ("↑↓/jk", "navigate"),
        ("Enter", "view"),
        ("/", "search"),
        ("r", "reload"),
        ("Esc", "close"),
    ];
    assert_eq!(search_field::hints(false, false, &list), list);
    assert_eq!(
        search_field::hints(true, false, &list),
        [
            ("↑↓", "navigate"),
            ("Enter", "view"),
            ("Esc", "leave search")
        ]
    );
    assert_eq!(
        search_field::hints(false, true, &list)[4],
        ("Esc", "clear search")
    );
}

fn config_app() -> App {
    let mut app = App::default();
    let fields: Vec<_> = ["alpha", "beta", "gamma"]
        .iter()
        .map(|name| json!({"name": name, "path": format!("/{name}"), "kind": "int", "value": 1}))
        .collect();
    let loaded = config_fields::parse(&json!({ "fields": fields }));
    config::apply_loaded(&mut app, loaded);
    app.config_screen.open = true;
    app
}

#[test]
fn config_navigates_with_j_k_and_searches_after_slash() {
    let client = Arc::new(Client::stub());
    let (tx, _rx) = mpsc::channel(1);
    let mut app = config_app();
    let send = |app: &mut App, code| config::handle_key(app, &client, &tx, key(code));

    send(&mut app, KeyCode::Char('j'));
    send(&mut app, KeyCode::Char('j'));
    send(&mut app, KeyCode::Char('k'));
    assert_eq!(app.config_screen.selected, 1);
    assert_eq!(app.config_screen.search.query, "", "j/k do not type");

    send(&mut app, KeyCode::Char('/'));
    for ch in "gamma".chars() {
        send(&mut app, KeyCode::Char(ch));
    }
    assert_eq!(config::filtered(&app)[0].name, "gamma");
    send(&mut app, KeyCode::Esc);
    assert!(app.config_screen.open);
    assert_eq!(app.config_screen.search.query, "gamma");
    send(&mut app, KeyCode::Esc);
    assert_eq!(app.config_screen.search.query, "");
    assert!(app.config_screen.open);
    send(&mut app, KeyCode::Esc);
    assert!(!app.config_screen.open);
}

#[test]
fn enter_in_the_search_field_views_the_highlighted_plugin() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    let state =
        serde_json::from_value(json!({"plugins": [{"name": "alpha"}, {"name": "beta"}]})).unwrap();
    plugins::apply_event(&mut app, plugins::Event::Read(Ok(Some(state))));

    plugins::handle_key(&mut app, &client, key(KeyCode::Char('/')));
    for ch in "jk".chars() {
        plugins::handle_key(&mut app, &client, key(KeyCode::Char(ch)));
    }
    assert_eq!(app.plugins.filter.query, "jk", "j/k type while searching");
    plugins::handle_key(&mut app, &client, key(KeyCode::Backspace));
    plugins::handle_key(&mut app, &client, key(KeyCode::Backspace));
    plugins::handle_key(&mut app, &client, key(KeyCode::Down));
    assert!(
        app.plugins.filter.focused,
        "arrows move the list, not the focus"
    );
    plugins::handle_key(&mut app, &client, key(KeyCode::Enter));
    assert_eq!(app.plugins.viewing.as_deref(), Some("beta"));
}

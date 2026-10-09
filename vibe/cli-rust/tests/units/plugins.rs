//! `/plugins` catalogue parsing, reload diff report, rows, and browser key flow.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::layout::Rect;
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::plugins::rows::{self, Row};
use vibe_rs::plugins::{self, text, Event};
use vibe_rs::server::{Client, PluginCatalogState};

fn entry(name: &str, digest: &str) -> Value {
    json!({
        "name": name,
        "version": "1.0.0",
        "sourceFormat": "agent_plugins_1_0",
        "manifestDigest": "m",
        "scope": "global",
        "contentSha256": digest,
        "installedRoot": format!("/opt/plugins/{name}"),
    })
}

fn catalog(value: Value) -> PluginCatalogState {
    serde_json::from_value(value).unwrap()
}

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

#[test]
fn catalogue_parses_camel_case_and_ignores_unknown_fields() {
    let state = catalog(json!({
        "plugins": [{"name": "devtools", "futureField": 1, "drifted": 2}],
        "dropped": [{"file": "/x/plugin.toml", "message": "bad manifest"}],
        "future": true,
    }));
    assert_eq!(state.plugins[0].name, "devtools");
    assert_eq!(state.plugins[0].drifted, 2);
    assert_eq!(state.plugins[0].installed_root, None);
    assert_eq!(state.dropped[0].message, "bad manifest");
}

#[test]
fn reload_report_lists_added_removed_and_repinned_plugins_by_name() {
    let before = catalog(json!({"plugins": [
        entry("alpha", "aaaaaaaa1111"), entry("beta", "bbbbbbbb2222"), entry("gamma", "cccccccc3333"),
    ]}));
    let after = catalog(json!({"plugins": [
        entry("alpha", "aaaaaaaa1111"), entry("beta", "dddddddd4444"), entry("delta", "eeeeeeee5555"),
    ]}));
    let changes = text::changes(&before, &after);
    assert_eq!(
        text::reload_report(&changes, &after),
        "### Plugins reloaded\n\n\
         - `~` `beta` bbbbbbbb → dddddddd\n\
         - `+` `delta` 1.0.0\n\
         - `-` `gamma` — no longer installed"
    );
    assert_eq!(
        text::reload_report(&text::changes(&after, &after), &after),
        text::NOTHING_CHANGED
    );
}

#[test]
fn detail_lines_group_components_and_flag_unknown_facts() {
    let mut value = entry("devtools", "0123456789abcdef");
    value["description"] = json!("Developer helpers");
    value["installedRoot"] = Value::Null;
    value["scope"] = Value::Null;
    value["components"] = json!([
        {"kind": "skill", "name": "lint"},
        {"kind": "tool", "name": "run", "status": "stale"},
        {"kind": "skill", "name": "fmt"},
    ]);
    let state = catalog(json!({"plugins": [value]}));
    assert_eq!(
        text::detail_lines(&state.plugins[0]),
        [
            "  Author: —",
            "  Version: 1.0.0",
            "",
            "  Developer helpers",
            "",
            "  Location: (uninstalled since pin)",
            "  Scope: —",
            "  Format: agent_plugins_1_0",
            "  Pinned: 01234567",
            "",
            "  Components:",
            "  ● Skills: lint, fmt",
            "  ● Tools: run (stale)",
        ]
    );
    assert_eq!(
        text::entry_facts(&state.plugins[0]),
        "— · agent_plugins_1_0 · 01234567"
    );
}

#[test]
fn filter_matches_name_or_description_and_keeps_dropped_files() {
    let mut app = App::default();
    let mut tools = entry("tools", "1");
    tools["description"] = json!("Git helpers");
    plugins::apply_event(
        &mut app,
        Event::Read(Ok(Some(catalog(json!({
            "plugins": [entry("devtools", "0"), tools],
            "dropped": [{"file": "/x/broken", "message": "invalid"}],
        }))))),
    );
    assert!(app.plugins.open);
    assert_eq!(rows::title(&app.plugins), "Plugins · 2 in this session");
    app.plugins.filter.query = " GIT ".to_owned();
    assert_eq!(
        rows::rows(&app.plugins),
        [
            Row::Entry(1),
            Row::Blank,
            Row::Heading("Not loaded"),
            Row::Dim("  ! /x/broken — invalid".to_owned()),
        ]
    );
    app.plugins.filter.query = "zzz".to_owned();
    assert_eq!(
        rows::rows(&app.plugins)[0],
        Row::Note("No plugins match this filter")
    );
}

#[test]
fn empty_or_missing_catalogues_never_open_the_browser() {
    let mut app = App::default();
    plugins::apply_event(&mut app, Event::Read(Ok(None)));
    plugins::apply_event(
        &mut app,
        Event::Read(Ok(Some(PluginCatalogState::default()))),
    );
    plugins::apply_event(&mut app, Event::Reloaded(Ok(None)));
    assert!(!app.plugins.open);
}

#[test]
fn keys_view_a_plugin_go_back_filter_and_close() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    let state = catalog(json!({"plugins": [entry("alpha", "1"), entry("beta", "2")]}));
    plugins::apply_event(&mut app, Event::Read(Ok(Some(state))));

    plugins::handle_key(&mut app, &client, key(KeyCode::Char('j')));
    plugins::handle_key(&mut app, &client, key(KeyCode::Enter));
    assert_eq!(app.plugins.viewing.as_deref(), Some("beta"));
    assert_eq!(rows::title(&app.plugins), "beta");
    plugins::handle_key(&mut app, &client, key(KeyCode::Char('/')));
    assert!(!app.plugins.filter.focused, "no filter in the detail view");
    plugins::handle_key(&mut app, &client, key(KeyCode::Esc));
    assert_eq!(
        app.plugins.viewing, None,
        "Esc backs out of the detail first"
    );
    assert!(app.plugins.open);
    assert_eq!(app.plugins.selected, 0);

    plugins::handle_key(&mut app, &client, key(KeyCode::Char('/')));
    for ch in "bet".chars() {
        plugins::handle_key(&mut app, &client, key(KeyCode::Char(ch)));
    }
    assert_eq!(rows::rows(&app.plugins), [Row::Entry(1)]);
    plugins::handle_key(&mut app, &client, key(KeyCode::Esc));
    assert!(!app.plugins.filter.focused, "Esc leaves the field first");
    assert_eq!(app.plugins.filter.query, "bet");
    plugins::handle_key(&mut app, &client, key(KeyCode::Esc));
    assert_eq!(app.plugins.filter.query, "", "then clears the filter");
    assert!(app.plugins.open);
    plugins::handle_key(&mut app, &client, key(KeyCode::Esc));
    assert!(!app.plugins.open);
}

#[test]
fn reload_that_removes_the_viewed_plugin_returns_to_the_list() {
    let mut app = App::default();
    let before = catalog(json!({"plugins": [entry("alpha", "1"), entry("beta", "2")]}));
    plugins::apply_event(&mut app, Event::Read(Ok(Some(before.clone()))));
    app.plugins.viewing = Some("alpha".to_owned());
    let after = catalog(json!({"plugins": [entry("beta", "3")]}));
    let changes = text::changes(&before, &after);
    plugins::apply_event(&mut app, Event::Reloaded(Ok(Some((changes, after)))));
    assert_eq!(app.plugins.viewing, None);
    assert_eq!(rows::rows(&app.plugins), [Row::Entry(0)]);
    assert_eq!(
        app.plugins.catalog.plugins[0].content_sha256.as_deref(),
        Some("3")
    );
}

#[test]
fn clicking_a_plugin_while_filtering_leaves_the_filter_for_the_detail() {
    let mut app = App::default();
    let state = catalog(json!({"plugins": [entry("alpha", "1"), entry("beta", "2")]}));
    plugins::apply_event(&mut app, Event::Read(Ok(Some(state))));
    app.plugins.filter.focused = true;
    app.plugins.list_area = Rect::new(0, 0, 40, 2);
    app.plugins.line_rows = vec![0, 1];
    plugins::click(&mut app, (4, 1), false);
    plugins::click(&mut app, (4, 1), true);
    assert_eq!(app.plugins.viewing.as_deref(), Some("beta"));
    assert!(!app.plugins.filter.focused);
    plugins::handle_key(&mut app, &Arc::new(Client::stub()), key(KeyCode::Esc));
    assert_eq!(
        app.plugins.viewing, None,
        "Esc goes back, not into the query"
    );
    assert!(app.plugins.open);
}

#[tokio::test]
async fn a_second_reload_waits_for_the_first_to_answer() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    plugins::reload(&mut app, &client);
    assert!(app.plugins.reloading);
    let pending = app
        .pending_commits
        .load(std::sync::atomic::Ordering::Relaxed);
    plugins::reload(&mut app, &client);
    assert_eq!(
        app.pending_commits
            .load(std::sync::atomic::Ordering::Relaxed),
        pending,
        "the second reload never starts"
    );
    plugins::apply_event(&mut app, Event::Reloaded(Err("boom".into())));
    assert!(!app.plugins.reloading);
}

#[test]
fn list_keys_follow_textual_option_list() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    let names = ["a", "b", "c", "d"].map(|name| entry(name, name));
    plugins::apply_event(
        &mut app,
        Event::Read(Ok(Some(catalog(json!({"plugins": names}))))),
    );
    app.plugins.list_area = Rect::new(0, 0, 40, 2);
    let mut press = |code| {
        plugins::handle_key(&mut app, &client, key(code));
        app.plugins.selected
    };
    assert_eq!(press(KeyCode::Up), 3, "up from the first wraps to the last");
    assert_eq!(
        press(KeyCode::Char('j')),
        0,
        "down from the last wraps to the first"
    );
    assert_eq!(press(KeyCode::PageDown), 2);
    assert_eq!(
        press(KeyCode::PageDown),
        3,
        "pages clamp instead of wrapping"
    );
    assert_eq!(press(KeyCode::Home), 0);
    assert_eq!(press(KeyCode::End), 3);
}

#[tokio::test]
async fn shortcuts_ignore_a_held_modifier_like_textual_bindings() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    let names = [entry("a", "1"), entry("b", "2")];
    plugins::apply_event(
        &mut app,
        Event::Read(Ok(Some(catalog(json!({"plugins": names}))))),
    );
    let ctrl = |ch| KeyEvent::new(KeyCode::Char(ch), KeyModifiers::CONTROL);
    plugins::handle_key(&mut app, &client, ctrl('j'));
    plugins::handle_key(&mut app, &client, ctrl('r'));
    plugins::handle_key(&mut app, &client, ctrl('/'));
    assert_eq!(app.plugins.selected, 0);
    assert!(!app.plugins.reloading);
    assert!(!app.plugins.filter.focused);
    let slash = KeyEvent::new(KeyCode::Char('/'), KeyModifiers::SHIFT);
    plugins::handle_key(&mut app, &client, slash);
    assert!(
        app.plugins.filter.focused,
        "Shift is how some layouts type `/`"
    );
}

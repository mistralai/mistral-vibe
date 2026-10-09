//! `/proxy-setup` drafts: server order, focus wrap, change diff, and paste.

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::chat_input::Action;
use vibe_rs::proxy_setup::{apply_read, paste, ProxySetupApp};
use vibe_rs::server::proto_proxy::ProxySettingsView;

fn settings() -> ProxySettingsView {
    serde_json::from_value(json!({
        "values": {"HTTP_PROXY": "http://old:8080", "HTTPS_PROXY": null, "NO_PROXY": "localhost"},
        "descriptions": {
            "HTTP_PROXY": "Proxy URL for HTTP requests",
            "HTTPS_PROXY": "Proxy URL for HTTPS requests",
            "NO_PROXY": "Comma-separated list of hosts to bypass proxy",
        },
    }))
    .unwrap()
}

fn type_text(state: &mut ProxySetupApp, text: &str) {
    let field = &mut state.inputs[state.focused].field;
    for c in text.chars() {
        field.edit(Action::Insert(c));
    }
}

#[test]
fn inputs_follow_the_server_order_with_saved_values() {
    let state = ProxySetupApp::new(settings());
    let keys: Vec<_> = state.inputs.iter().map(|i| i.key.as_str()).collect();
    assert_eq!(keys, ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]);
    assert_eq!(state.inputs[0].field.text, "http://old:8080");
    assert_eq!(state.inputs[1].field.text, "");
    assert_eq!(state.inputs[1].description, "Proxy URL for HTTPS requests");
    assert!(state.open);
    assert_eq!(state.focused, 0);
}

#[test]
fn focus_wraps_both_ways() {
    let mut state = ProxySetupApp::new(settings());
    state.move_focus(false);
    assert_eq!(state.focused, 2);
    state.move_focus(true);
    assert_eq!(state.focused, 0);
}

#[test]
fn unchanged_drafts_produce_no_changes() {
    let state = ProxySetupApp::new(settings());
    assert!(state.changes().is_empty());
}

#[test]
fn changes_are_trimmed_and_an_emptied_value_unsets() {
    let mut state = ProxySetupApp::new(settings());
    type_text(&mut state, "http://new:9090");
    state.move_focus(true);
    type_text(&mut state, "  https://new:9443  ");
    state.move_focus(true);
    state.inputs[2].field.edit(Action::DeleteLeft);
    let changes = state.changes();
    assert_eq!(changes.len(), 3);
    assert_eq!(changes["HTTP_PROXY"], json!("http://new:9090"));
    assert_eq!(changes["HTTPS_PROXY"], json!("https://new:9443"));
    assert_eq!(changes["NO_PROXY"], Value::Null);
}

#[test]
fn whitespace_only_draft_on_an_unset_value_is_no_change() {
    let mut state = ProxySetupApp::new(settings());
    state.move_focus(true);
    type_text(&mut state, "   ");
    assert!(state.changes().is_empty());
}

#[test]
fn paste_keeps_the_first_line_in_the_focused_input() {
    let mut app = App::default();
    apply_read(&mut app, Ok(settings()));
    app.proxy_setup.move_focus(true);
    paste(&mut app, "https://pasted:1\nsecond line");
    assert_eq!(app.proxy_setup.inputs[1].field.text, "https://pasted:1");
}

#[test]
fn failed_read_keeps_the_app_closed() {
    let mut app = App::default();
    apply_read(&mut app, Err("Permission denied".into()));
    assert!(!app.proxy_setup.open);
    assert!(!app.proxy_setup.loading);
}

#[test]
fn paste_stops_at_a_carriage_return() {
    let mut app = App::default();
    apply_read(&mut app, Ok(settings()));
    app.proxy_setup.move_focus(true);
    paste(&mut app, "https://pasted:1\rsecond line");
    assert_eq!(app.proxy_setup.inputs[1].field.text, "https://pasted:1");
}

#[test]
fn a_reply_for_another_session_is_dropped() {
    let mut app = App::default();
    app.proxy_setup.loading = true;
    app.proxy_setup.session_id = Some("old".into());
    app.session.session_id = Some("new".into());
    apply_read(&mut app, Ok(settings()));
    assert!(!app.proxy_setup.open);
    assert!(!app.proxy_setup.loading);
}

#[test]
fn scroll_follows_the_focused_input() {
    let mut state = ProxySetupApp::new(settings());
    assert_eq!(state.reconcile_scroll(3), 0);
    state.move_focus(false);
    assert_eq!(state.reconcile_scroll(3), 3);
    state.move_focus(true);
    assert_eq!(state.reconcile_scroll(3), 0);
}

#[test]
fn wheel_scrolls_freely_within_bounds_until_focus_moves() {
    let mut state = ProxySetupApp::new(settings());
    state.wheel(false, 10);
    assert_eq!(state.reconcile_scroll(4), 2);
    state.wheel(true, 1);
    assert_eq!(state.reconcile_scroll(4), 1);
    state.focus(0);
    assert_eq!(state.reconcile_scroll(4), 0);
}

#[test]
fn a_single_visible_line_shows_the_focused_input_not_its_label() {
    let mut state = ProxySetupApp::new(settings());
    assert_eq!(state.reconcile_scroll(1), 1);
    state.move_focus(true);
    assert_eq!(state.reconcile_scroll(1), 3);
    state.move_focus(false);
    assert_eq!(state.reconcile_scroll(1), 1);
}

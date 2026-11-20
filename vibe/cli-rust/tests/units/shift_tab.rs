//! Shift+Tab moves back wherever Tab moves through something, and cycles approval policies elsewhere.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::config::ConfigField;
use vibe_rs::focus::tab_navigates;
use vibe_rs::input::handle_priority_key;
use vibe_rs::loops::{self, Form};
use vibe_rs::proxy_setup::ProxySetupApp;
use vibe_rs::server::Client;
use vibe_rs::skills_browser::{self, Tab};

use crate::skills_browser_support::{opened, registry, shared};
use crate::support::key;

fn back_tab() -> KeyEvent {
    KeyEvent::new(KeyCode::BackTab, KeyModifiers::SHIFT)
}

fn ready() -> App {
    let mut app = App::default();
    vibe_rs::agents::apply_runtime(
        &mut app,
        &json!({"runtime": {
        "activeApprovalPolicy": {"name": "ask", "displayName": "ask"},
        "approvalPolicies": [
            {"name": "ask", "displayName": "ask"},
            {"name": "accept-edits", "displayName": "accept-edits"},
        ]}}),
    );
    app.session.session_id = Some("session".into());
    app.set_status(Status::Ready);
    app
}

#[tokio::test]
async fn shift_tab_cycles_approval_policies_where_tab_navigates_nothing() {
    let mut app = ready();
    assert!(!tab_navigates(&app));
    assert_eq!(
        handle_priority_key(&mut app, &Arc::new(Client::stub()), back_tab()),
        Some(false)
    );
    assert_eq!(app.agents.desired.as_deref(), Some("accept-edits"));
    // The loop list takes no Tab, so Shift+Tab still switches policies over it.
    let mut app = ready();
    loops::open(&mut app);
    assert!(!tab_navigates(&app));
}

#[test]
fn shift_tab_moves_back_through_the_loop_form_fields() {
    let client = Arc::new(Client::stub());
    let mut app = ready();
    loops::open(&mut app);
    app.loops.form = Some(Form::default());
    assert!(tab_navigates(&app));
    assert_eq!(handle_priority_key(&mut app, &client, back_tab()), None);
    assert!(app.agents.desired.is_none());
    loops::handle_key(&mut app, &client, back_tab());
    assert_eq!(
        app.loops.form.as_ref().unwrap().focused,
        2,
        "wraps to the last field"
    );
    loops::handle_key(&mut app, &client, key(KeyCode::Tab));
    assert_eq!(app.loops.form.as_ref().unwrap().focused, 0);
}

#[test]
fn shift_tab_moves_back_through_the_proxy_inputs() {
    let client = Arc::new(Client::stub());
    let mut app = ready();
    app.proxy_setup = ProxySetupApp::new(
        serde_json::from_value(
            json!({"values": {}, "descriptions": {"A": "a", "B": "b", "C": "c"}}),
        )
        .unwrap(),
    );
    assert!(tab_navigates(&app));
    assert_eq!(handle_priority_key(&mut app, &client, back_tab()), None);
    vibe_rs::proxy_setup::handle_key(&mut app, &client, back_tab());
    assert_eq!(app.proxy_setup.focused, 2, "wraps to the last input");
}

#[test]
fn shift_tab_switches_the_skills_tabs_back() {
    let client = Arc::new(Client::stub());
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    assert!(tab_navigates(&app));
    assert_eq!(handle_priority_key(&mut app, &client, back_tab()), None);
    skills_browser::handle_key(&mut app, &client, back_tab());
    assert_eq!(
        app.skills_browser.tab,
        Tab::Shared,
        "wraps to the other tab"
    );
    skills_browser::handle_key(&mut app, &client, back_tab());
    assert_eq!(app.skills_browser.tab, Tab::Mine);
}

#[test]
fn shift_tab_moves_the_config_save_target_back() {
    let client = Arc::new(Client::stub());
    let (tx, _rx) = mpsc::channel(1);
    let mut app = ready();
    app.config_screen.open = true;
    app.config_screen.targets = vec!["user-toml".into(), "project".into(), "overrides".into()];
    vibe_rs::config_edit::open(
        &mut app,
        ConfigField {
            name: "integer".into(),
            path: "/integer".into(),
            kind: "int".into(),
            raw_value: json!(1),
            popular: false,
            writable: true,
            overridden: false,
            enum_choices: Vec::new(),
            description: String::new(),
            value_labels: Default::default(),
            layers: Vec::new(),
        },
    );
    assert!(tab_navigates(&app));
    assert_eq!(handle_priority_key(&mut app, &client, back_tab()), None);
    let target = |app: &App| app.config_screen.edit.as_ref().unwrap().target;
    assert_eq!(target(&app), 0);
    vibe_rs::config_edit::handle_key(&mut app, &client, &tx, back_tab());
    assert_eq!(target(&app), 2, "wraps to the last target");
    vibe_rs::config_edit::handle_key(&mut app, &client, &tx, back_tab());
    assert_eq!(target(&app), 1);
}

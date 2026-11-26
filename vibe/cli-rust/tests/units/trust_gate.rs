//! Mid-session trust gate: cancelling sends nothing, trusting persists the decision.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;
use tokio::sync::mpsc::UnboundedReceiver;
use vibe_rs::app::App;
use vibe_rs::server::{Client, WorkspaceTrustDetails};
use vibe_rs::trust_gate::{self, Event, TrustNext};

use crate::support::{ctrl, key};

fn gate() -> (App, Arc<Client>, UnboundedReceiver<Value>) {
    let (client, frames) = Client::stub_silent();
    let client = Arc::new(client);
    let mut app = App::default();
    app.set_session_id("session".into());
    let details = WorkspaceTrustDetails {
        cwd: "/work/untrusted".into(),
        available_decisions: vec!["trust_cwd".into(), "decline".into()],
        ..Default::default()
    };
    let next = TrustNext::Cd {
        target: "/work/untrusted".into(),
        session_id: "session".into(),
        ticket: 0,
    };
    trust_gate::apply_event(&mut app, &client, Event::Status(next, Ok(Some(details))));
    app.trust.accepts_at = None;
    (app, client, frames)
}

async fn nothing_sent(frames: &mut UnboundedReceiver<Value>) {
    let sent = tokio::time::timeout(Duration::from_millis(50), frames.recv()).await;
    assert!(sent.is_err(), "nothing reaches the wire: {sent:?}");
}

#[tokio::test]
async fn esc_cancels_without_a_decision() {
    let (mut app, client, mut frames) = gate();
    assert!(app.trust.open && app.trust.pending.is_some());
    assert!(!trust_gate::handle_key(
        &mut app,
        &client,
        key(KeyCode::Esc)
    ));
    assert!(!app.trust.open && app.trust.pending.is_none());
    nothing_sent(&mut frames).await;
}

#[tokio::test]
async fn ctrl_c_cancels_instead_of_quitting() {
    let (mut app, client, mut frames) = gate();
    assert!(!trust_gate::handle_key(&mut app, &client, ctrl('c')));
    assert!(!app.trust.open);
    nothing_sent(&mut frames).await;
}

#[tokio::test]
async fn dont_trust_persists_nothing() {
    let (mut app, client, mut frames) = gate();
    let decline = KeyEvent::new(KeyCode::Char('2'), KeyModifiers::NONE);
    trust_gate::handle_key(&mut app, &client, decline);
    assert!(!app.trust.open && app.trust.pending.is_none());
    nothing_sent(&mut frames).await;
}

#[tokio::test]
async fn trusting_sends_the_decision_without_a_session() {
    let (mut app, client, mut frames) = gate();
    trust_gate::handle_key(&mut app, &client, key(KeyCode::Enter));
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("a decision is sent")
        .expect("a frame");
    assert_eq!(frame["method"], "workspace/trust/decision");
    assert_eq!(frame["params"]["decision"], "trust_cwd");
    assert_eq!(frame["params"]["cwd"], "/work/untrusted");
    assert_eq!(frame["params"]["sessionId"], Value::Null);
}

#[test]
fn the_startup_gate_still_quits_on_ctrl_c() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    assert!(trust_gate::handle_key(&mut app, &client, ctrl('c')));
}

#[tokio::test]
async fn a_cd_for_a_session_switched_away_from_opens_no_gate() {
    let (client, mut frames) = Client::stub_silent();
    let client = Arc::new(client);
    let mut app = App::default();
    app.set_session_id("other".into());
    let details = WorkspaceTrustDetails {
        cwd: "/work/untrusted".into(),
        available_decisions: vec!["trust_cwd".into(), "decline".into()],
        ..Default::default()
    };
    let next = TrustNext::Cd {
        target: "/work/untrusted".into(),
        session_id: "session".into(),
        ticket: 0,
    };
    trust_gate::apply_event(&mut app, &client, Event::Status(next, Ok(Some(details))));
    assert!(!app.trust.open && app.trust.pending.is_none());
    nothing_sent(&mut frames).await;
}

#[tokio::test]
async fn a_cd_a_later_cd_replaced_opens_no_gate() {
    let (client, mut frames) = Client::stub_silent();
    let client = Arc::new(client);
    let mut app = App::default();
    app.set_session_id("session".into());
    // A second `/cd` ran after the first: only its answer may apply.
    app.session.cd_ticket = 2;
    let details = WorkspaceTrustDetails {
        cwd: "/work/untrusted".into(),
        available_decisions: vec!["trust_cwd".into(), "decline".into()],
        ..Default::default()
    };
    let older = TrustNext::Cd {
        target: "/work/untrusted".into(),
        session_id: "session".into(),
        ticket: 1,
    };
    trust_gate::apply_event(&mut app, &client, Event::Status(older, Ok(Some(details))));
    assert!(!app.trust.open && app.trust.pending.is_none());
    nothing_sent(&mut frames).await;
    assert!(vibe_rs::commands::cd::current(&app, "session", 2));
    assert!(!vibe_rs::commands::cd::current(&app, "session", 1));
}

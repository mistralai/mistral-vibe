//! Sessions page actions: refusals, archive outcomes, and adopting an opened session.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;

use vibe_rs::app::{App, Severity, Status};
use vibe_rs::server::PublicSessionState;
use vibe_rs::sessions_page::actions::{self, Event};
use vibe_rs::sessions_page::{keys, leases};

use crate::feedback_support::{errors, notice, warnings};
use crate::support::{stub, with_page};

const BUSY: &str = "This session is open in another Vibe process. Close it there to continue here.";

fn press(app: &mut App, code: KeyCode) {
    keys::handle_key(app, &stub(), KeyEvent::from(code));
}

fn lock(app: &mut App) {
    leases::on_updated(
        app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": {"processId": 4242}}),
    );
}

#[test]
fn archiving_a_locked_row_warns_with_the_busy_refusal() {
    let mut app = with_page();
    lock(&mut app);
    press(&mut app, KeyCode::Char('a'));
    assert_eq!(notice(&app), Some((Severity::Warning, BUSY)));
    assert!(warnings(&app).is_empty());
}

#[test]
fn the_page_neither_deletes_nor_pins() {
    let mut app = with_page();
    press(&mut app, KeyCode::Char('d'));
    press(&mut app, KeyCode::Char('d'));
    press(&mut app, KeyCode::Char('p'));
    assert!(app.sessions_page.busy.is_none());
    assert_eq!(notice(&app), None);
    assert_eq!(app.sessions_page.sessions.len(), 1);
}

#[test]
fn the_current_session_cannot_be_archived() {
    let mut app = with_page();
    app.set_session_id("busy".into());
    press(&mut app, KeyCode::Char('a'));
    assert_eq!(
        notice(&app),
        Some((Severity::Warning, "The current session can't be archived."))
    );
}

#[test]
fn an_archived_session_says_where_it_went() {
    let mut app = with_page();
    let event = Event::Archived {
        id: "busy".into(),
        answer: Ok(Some(1)),
    };
    actions::apply_event(&mut app, &stub(), event);
    assert!(app.sessions_page.view.rows.is_empty());
    assert_eq!(
        notice(&app),
        Some((
            Severity::Information,
            "Archived Saved implementation. Press . to see archived sessions."
        ))
    );
}

#[test]
fn a_restored_session_needs_no_notice() {
    let mut app = with_page();
    app.sessions_page.sessions[0].archived_at = Some(1);
    let event = Event::Archived {
        id: "busy".into(),
        answer: Ok(None),
    };
    actions::apply_event(&mut app, &stub(), event);
    assert_eq!(app.sessions_page.sessions[0].archived_at, None);
    assert_eq!(notice(&app), None);
}

#[test]
fn opening_while_a_turn_runs_is_refused() {
    let mut app = with_page();
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    press(&mut app, KeyCode::Enter);
    assert!(app.sessions_page.busy.is_none());
    assert!(app.sessions_page.open);
    let (severity, _) = notice(&app).expect("a refusal");
    assert_eq!(severity, Severity::Warning);
}

#[test]
fn opening_the_current_session_just_closes_the_page() {
    let mut app = with_page();
    app.set_session_id("busy".into());
    press(&mut app, KeyCode::Right);
    assert!(!app.sessions_page.open);
}

#[tokio::test]
async fn an_open_answering_after_esc_is_dropped() {
    let mut app = with_page();
    app.sessions_page.busy = Some("busy".into());
    keys::handle_key(
        &mut app,
        &stub(),
        KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
    );
    assert!(!app.sessions_page.open);
    actions::apply_event(&mut app, &stub(), Event::Opened(0, Ok(Box::new(state()))));
    assert_eq!(app.session.session_id, None);
    assert!(!screen_text(&app).contains("Saved work"));
}

#[tokio::test]
async fn an_opened_session_replaces_the_transcript_and_drops_the_startup_warnings() {
    let mut app = with_page();
    app.sessions_page.busy = Some("busy".into());
    let _channels = vibe_rs::channels::connect(&mut app);
    vibe_rs::notices::warn(&mut app, "hooks.toml: Failed to parse");
    actions::apply_event(&mut app, &stub(), Event::Opened(0, Ok(Box::new(state()))));
    assert!(!app.sessions_page.open);
    assert_eq!(app.session.session_id.as_deref(), Some("busy"));
    assert_eq!(app.session.cwd.as_deref(), Some("/work/other"));
    assert!(!screen_text(&app).contains("Failed to parse"));
    assert!(screen_text(&app).contains("Saved work"));
}

#[test]
fn a_failed_open_keeps_the_page() {
    let mut app = with_page();
    app.sessions_page.busy = Some("busy".into());
    let event = Event::Opened(0, Err("Failed to resume session: gone".into()));
    actions::apply_event(&mut app, &stub(), event);
    assert!(app.sessions_page.open);
    assert!(app.sessions_page.busy.is_none());
    assert_eq!(
        notice(&app),
        Some((Severity::Error, "Failed to resume session: gone"))
    );
}

fn state() -> PublicSessionState {
    serde_json::from_value(json!({
        "eventId": 1,
        "session": {"id": "busy", "cwd": "/work/other"},
        "history": [{
            "id": "m1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Saved work"}],
        }],
    }))
    .unwrap()
}

fn screen_text(app: &App) -> String {
    app.view
        .transcript
        .lines()
        .filter_map(|entry| match entry.entry {
            vibe_rs::server::HistoryEntry::Message(message) => {
                message.content.first().and_then(|content| match content {
                    vibe_rs::server::MessageContent::Text { text } => Some(text.clone()),
                    _ => None,
                })
            }
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn a_trust_gate_settling_after_the_page_closed_opens_nothing() {
    let mut app = with_page();
    app.sessions_page.busy = Some("busy".into());
    keys::handle_key(
        &mut app,
        &stub(),
        KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
    );
    let trusted = vibe_rs::trust_gate::Outcome::Trusted;
    actions::after_trust_open(&mut app, &stub(), "busy".into(), 0, trusted);
    assert!(!vibe_rs::sessions_page::switching(&app));
    assert!(!app.view.command_loading);
}

#[tokio::test]
async fn a_switch_answered_after_its_cancel_puts_the_conversation_back() {
    let mut app = with_page();
    app.sessions_page.busy = Some("busy".into());
    let trusted = vibe_rs::trust_gate::Outcome::Trusted;
    actions::after_trust_open(&mut app, &stub(), "busy".into(), 0, trusted);
    assert!(vibe_rs::sessions_page::switching(&app));
    vibe_rs::sessions_page::close(&mut app);
    actions::apply_event(&mut app, &stub(), Event::Opened(0, Ok(Box::new(state()))));
    assert!(!vibe_rs::sessions_page::switching(&app));
    assert!(!app.view.command_loading);
    assert_eq!(app.session.session_id, None);
}

#[tokio::test]
async fn an_open_replaced_by_a_later_one_never_takes_over() {
    let mut app = with_page();
    app.sessions_page.busy = Some("busy".into());
    let trusted = vibe_rs::trust_gate::Outcome::Trusted;
    actions::after_trust_open(&mut app, &stub(), "busy".into(), 0, trusted);
    // Esc, then a second open from the reopened page.
    app.sessions_page.ticket = 1;
    actions::apply_event(&mut app, &stub(), Event::Opened(0, Ok(Box::new(state()))));
    assert_eq!(app.session.session_id, None);
    assert!(
        vibe_rs::sessions_page::switching(&app),
        "the later open still loads"
    );
}

#[test]
fn a_list_failure_from_an_older_load_is_dropped() {
    let mut app = with_page();
    app.sessions_page.generation = 2;
    app.sessions_page.loading = true;
    let stale = vibe_rs::sessions_page::Event::Failed {
        generation: 1,
        message: "Failed to list sessions: gone".into(),
    };
    vibe_rs::sessions_page::apply_event(&mut app, &stub(), stale);
    assert!(
        app.sessions_page.loading,
        "the newer load keeps its spinner"
    );
    assert!(errors(&app).is_empty());
    let current = vibe_rs::sessions_page::Event::Failed {
        generation: 2,
        message: "Failed to list sessions: gone".into(),
    };
    vibe_rs::sessions_page::apply_event(&mut app, &stub(), current);
    assert!(!app.sessions_page.loading);
}

#[test]
fn a_projects_answer_from_an_older_load_is_dropped() {
    let mut app = with_page();
    app.sessions_page.generation = 2;
    let project = |id: &str| vibe_rs::sessions_page::Project {
        id: id.into(),
        ..Default::default()
    };
    let stale = vibe_rs::sessions_page::Event::Projects {
        generation: 1,
        projects: vec![project("old")],
    };
    vibe_rs::sessions_page::apply_event(&mut app, &stub(), stale);
    assert!(app.sessions_page.projects.is_empty());
    let current = vibe_rs::sessions_page::Event::Projects {
        generation: 2,
        projects: vec![project("current")],
    };
    vibe_rs::sessions_page::apply_event(&mut app, &stub(), current);
    assert_eq!(app.sessions_page.projects, vec![project("current")]);
}

#[test]
fn a_session_busy_refusal_from_the_server_is_a_warning() {
    let mut app = with_page();
    app.sessions_page.busy = Some("busy".into());
    let ticket = app.sessions_page.ticket;
    let event = Event::Opened(ticket, Err(BUSY.into()));
    actions::apply_event(&mut app, &stub(), event);
    assert_eq!(notice(&app), Some((Severity::Warning, BUSY)));
}

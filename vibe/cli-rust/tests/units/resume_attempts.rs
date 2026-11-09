//! Picker resume attempts: failures go to the inline notice, and a successful resume drops the startup warnings.

use crossterm::event::{KeyCode, KeyEvent};
use serde_json::json;

use vibe_rs::app::{App, Severity};
use vibe_rs::resume_picker::{self, Event};
use vibe_rs::server::PublicSessionState;

use crate::feedback_support::{errors, notice, warnings};
use crate::support::{stub, with_picker};

const BUSY: &str = "This session is open in another Vibe process. Close it there to continue here.";

#[test]
fn deleting_a_locked_row_warns_with_the_busy_refusal() {
    let mut app = with_picker();
    lock(&mut app);
    resume_picker::handle_key(&mut app, &stub(), KeyEvent::from(KeyCode::Char('d')));
    assert!(app.resume_picker.delete_confirm.is_none());
    assert!(app.resume_picker.deleting.is_none());
    assert_eq!(notice(&app), Some((Severity::Warning, BUSY)));
    assert!(warnings(&app).is_empty());
}

#[test]
fn a_failed_delete_shows_an_error_notice() {
    let mut app = with_picker();
    app.resume_picker.deleting = Some("busy".into());
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Deleted {
            id: "busy".into(),
            error: Some("Session is already open".into()),
        },
    );
    assert!(app.resume_picker.deleting.is_none());
    assert_eq!(
        notice(&app),
        Some((
            Severity::Error,
            "Failed to delete session: Session is already open"
        ))
    );
    assert!(errors(&app).is_empty());
}

#[test]
fn a_successful_resume_drops_the_startup_warnings() {
    let mut app = with_picker();
    let _channels = vibe_rs::channels::connect(&mut app);
    vibe_rs::notices::warn(&mut app, "hooks.toml: Failed to parse");
    app.resume_picker.resuming = true;
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: "busy".into(),
            state: state(),
        },
    );
    assert!(!screen_text(&app).contains("Failed to parse"));
    assert!(screen_text(&app).contains("Resumed session"));
}

fn lock(app: &mut App) {
    resume_picker::on_lease_updated(
        app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": {"processId": 4242}}),
    );
}

fn state() -> PublicSessionState {
    serde_json::from_value(json!({
        "eventId": 1,
        "session": {"id": "busy"},
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

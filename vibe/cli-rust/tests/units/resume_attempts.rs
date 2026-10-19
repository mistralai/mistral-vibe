//! Resume attempts: a locked row's delete refuses with a toast, a failed
//! delete toasts, and a successful resume drops the stale attempt notices.

use crossterm::event::{KeyCode, KeyEvent};
use serde_json::json;
use std::sync::Arc;

use vibe_rs::app::App;
use vibe_rs::resume_picker::{self, Event, Session};
use vibe_rs::server::{Client, PublicSessionState};

const BUSY: &str = "This session is open in another Vibe process. Close it there to continue here.";

#[test]
fn deleting_a_locked_row_toasts_the_busy_refusal() {
    let mut app = picker();
    lock(&mut app);
    resume_picker::handle_key(&mut app, &stub(), KeyEvent::from(KeyCode::Char('d')));
    assert!(app.resume_picker.delete_confirm.is_none());
    assert!(app.resume_picker.deleting.is_none());
    assert!(toast_texts(&app).contains(&BUSY.to_owned()));
}

#[test]
fn a_failed_delete_toasts_instead_of_mounting_a_notice() {
    let mut app = picker();
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
    assert!(
        toast_texts(&app).contains(&"Failed to delete session: Session is already open".to_owned())
    );
    assert!(!screen_text(&app).contains("Failed to delete session"));
}

#[test]
fn a_successful_resume_drops_the_stale_attempt_error() {
    let mut app = picker();
    let _channels = vibe_rs::channels::connect(&mut app);
    // A previous attempt's refusal is still mounted on the live transcript.
    app.view.transcript.add(&json!({
        "entry": {
            "id": "rs-resume-attempt-error",
            "type": "message",
            "role": "command_error",
            "content": [{"type": "text", "text": BUSY}],
            "generationStatus": "completed",
            "local": true,
        }
    }));
    app.resume_picker.resuming = true;
    app.commit_started();
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: "busy".into(),
            state: state(),
        },
    );
    assert!(!screen_text(&app).contains(BUSY));
    assert!(screen_text(&app).contains("Resumed session"));
}

fn picker() -> App {
    let mut app = App::default();
    app.resume_picker.open = true;
    app.resume_picker.sessions = vec![Session {
        id: "busy".into(),
        title: "Saved implementation".into(),
        ..Default::default()
    }];
    app.resume_picker.list.highlight(0);
    app
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

fn toast_texts(app: &App) -> Vec<String> {
    app.overlays
        .toasts
        .iter()
        .map(|toast| toast.text.clone())
        .collect()
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

fn stub() -> Arc<Client> {
    Arc::new(Client::stub())
}

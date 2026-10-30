//! Deleting saved sessions from the resume picker: a failed delete and the last one.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::resume_picker::{self, Event, Session};
use vibe_rs::server::{request_error, Client, Incoming, RequestFailure};

use crate::support::{message, messages, settle};

const CURRENT: &str = "11111111-current";
const SAVED: &str = "22222222-saved";

struct Picker {
    app: App,
    client: Arc<Client>,
    frames: mpsc::UnboundedReceiver<Value>,
    _events: mpsc::Receiver<vibe_rs::ui_event::UiEvent>,
}

impl Picker {
    /// `/resume` open on `listed`, over a conversation in the current session.
    fn open(listed: &[&str]) -> Self {
        let mut app = App::default();
        app.session.session_id = Some(CURRENT.into());
        app.view
            .transcript
            .add(&json!({"entry": message("current-row", "user", "Current question")}));
        let (tx, events) = mpsc::channel(8);
        app.events = Some(tx);
        let (client, frames) = Client::stub_answering();
        let client = Arc::new(client);
        resume_picker::open(&mut app, &client);
        let sessions = listed
            .iter()
            .map(|id| Session {
                id: (*id).into(),
                ..Default::default()
            })
            .collect();
        resume_picker::apply_event(&mut app, &client, Event::Loaded(sessions));
        Self {
            app,
            client,
            frames,
            _events: events,
        }
    }

    fn press(&mut self, code: KeyCode) {
        resume_picker::handle_key(&mut self.app, &self.client, KeyEvent::from(code));
    }

    fn apply(&mut self, event: Event) {
        resume_picker::apply_event(&mut self.app, &self.client, event);
    }

    async fn deletes_sent(&mut self) -> Vec<Value> {
        settle().await;
        std::iter::from_fn(|| self.frames.try_recv().ok())
            .filter(|frame| frame["method"] == "session/delete")
            .map(|frame| frame["params"]["sessionId"].clone())
            .collect()
    }
}

fn row(role: &str, text: &str) -> (String, String) {
    (role.to_owned(), text.to_owned())
}

#[tokio::test]
async fn a_failed_delete_keeps_the_row_and_toasts_the_error() {
    let mut picker = Picker::open(&[SAVED, CURRENT]);
    picker.press(KeyCode::Up);
    picker.press(KeyCode::Char('d'));
    picker.press(KeyCode::Char('d'));
    assert_eq!(picker.deletes_sent().await, [json!(SAVED)]);

    picker.apply(Event::Deleted {
        id: SAVED.into(),
        error: Some("Session store unavailable".into()),
    });

    assert!(picker.app.resume_picker.open);
    assert!(picker.app.resume_picker.deleting.is_none());
    let toasts: Vec<&str> = picker
        .app
        .overlays
        .toasts
        .iter()
        .map(|toast| toast.text.as_str())
        .collect();
    assert!(toasts.contains(&"Failed to delete session: Session store unavailable"));
    let ids: Vec<&str> = picker
        .app
        .resume_picker
        .sessions
        .iter()
        .map(|session| session.id.as_str())
        .collect();
    assert_eq!(ids, [SAVED, CURRENT]);

    picker.press(KeyCode::Char('d'));
    picker.press(KeyCode::Char('d'));
    assert_eq!(
        picker.deletes_sent().await,
        [json!(SAVED)],
        "the row can be deleted again"
    );

    picker.apply(Event::Deleted {
        id: SAVED.into(),
        error: Some("Session store unavailable".into()),
    });
    picker.press(KeyCode::Esc);
    assert!(!picker.app.resume_picker.open);
    let texts = messages(&picker.app);
    assert!(!texts.iter().any(|(role, text)| {
        role == "command_error" && text.starts_with("Failed to delete session")
    }));
    assert!(texts.contains(&row("user", "Current question")));
}

#[tokio::test]
async fn deleting_the_last_listed_session_reports_it_and_closes_the_picker() {
    let mut picker = Picker::open(&[SAVED]);
    picker.press(KeyCode::Char('d'));
    picker.press(KeyCode::Char('d'));
    assert_eq!(picker.deletes_sent().await, [json!(SAVED)]);

    picker.apply(Event::Deleted {
        id: SAVED.into(),
        error: None,
    });

    assert!(!picker.app.resume_picker.open);
    assert!(picker.app.resume_picker.sessions.is_empty());
    let texts = messages(&picker.app);
    let tail = &texts[texts.len() - 2..];
    assert_eq!(
        tail,
        [
            row("command_status", "Deleted session `22222222`."),
            row(
                "command_status",
                "No saved sessions left for this directory."
            ),
        ]
    );
    assert!(texts.contains(&row("user", "Current question")));
}

#[tokio::test]
async fn the_current_session_is_never_deleted() {
    let mut picker = Picker::open(&[SAVED, CURRENT]);
    picker.press(KeyCode::Char('d'));
    picker.press(KeyCode::Char('d'));

    assert_eq!(picker.deletes_sent().await, Vec::<Value>::new());
    assert!(picker.app.resume_picker.open);
    assert_eq!(picker.app.resume_picker.sessions.len(), 2);
}

/// `Client::request` wraps failures as `"{method} failed"` context over the
/// `RequestFailure`; the delete toast must carry the server's own message.
#[test]
fn a_delete_failure_carries_the_servers_message() {
    let frame = r#"{"jsonrpc":"2.0","id":1,"error":{"code":"conflict","message":"Session is already open: 22222222-saved","data":{"harnessCode":"session_busy","details":{"session_id":"22222222-saved"}}}}"#;
    let incoming: Incoming = serde_json::from_str(frame).expect("frame parses");
    let error = anyhow::Error::new(RequestFailure::Rpc(
        incoming.error.expect("frame carries an error"),
    ))
    .context("session/delete failed");
    assert_eq!(
        request_error::describe(&error),
        "Session is already open: 22222222-saved"
    );
}

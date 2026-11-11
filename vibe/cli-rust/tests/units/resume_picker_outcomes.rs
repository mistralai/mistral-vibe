//! What a picker resume leaves behind when it succeeds, fails or is cancelled.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::app::{App, Severity, Status};
use vibe_rs::resume_picker::{self, Event, Session};
use vibe_rs::server::PublicSessionState;

use crate::feedback_support::notice;
use crate::support::{message, messages, session_state, stub};

pub const CURRENT: &str = "current-session";
pub const SAVED: &str = "saved-session-1";

pub fn saved_state() -> PublicSessionState {
    session_state(SAVED, vec![message("saved-user", "user", "Saved question")])
}

pub fn row(role: &str, text: &str) -> (String, String) {
    (role.to_owned(), text.to_owned())
}

/// A ready session showing its own history.
pub fn ready() -> App {
    let mut app = App::default();
    app.set_session_id(CURRENT.to_owned());
    app.set_status(Status::Ready);
    app.view.transcript.load_snapshot(&session_state(
        CURRENT,
        vec![message("current-user", "user", "Current question")],
    ));
    app
}

pub fn open_picker(app: &mut App) {
    resume_picker::open(app, &stub());
    app.resume_picker.sessions = vec![Session {
        id: SAVED.into(),
        ..Session::default()
    }];
}

/// The picker open on a saved session.
pub fn picking() -> App {
    let mut app = ready();
    open_picker(&mut app);
    app
}

/// The picker open while a server-started turn (e.g. a scheduled loop) runs.
pub fn picking_while_generating() -> App {
    let mut app = picking();
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    app
}

/// Enter was pressed: the `session/resume` answer is in flight.
pub fn resuming(app: &mut App) {
    app.resume_picker.resuming = true;
}

pub fn preview(app: &mut App) {
    app.resume_picker.previewing = true;
    let request = app.resume_picker.preview_request;
    resume_picker::apply_event(
        app,
        &stub(),
        Event::Preview {
            request,
            state: saved_state(),
        },
    );
    assert!(messages(app).contains(&row("user", "Saved question")));
}

#[tokio::test]
async fn a_successful_resume_retires_the_running_turn() {
    let mut app = picking_while_generating();
    resuming(&mut app);
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: SAVED.into(),
            state: saved_state(),
        },
    );
    assert!(matches!(app.session.status, Status::Ready));
    assert!(app.queue.is_empty());
    assert!(!app.resume_picker.open);
    assert!(!app.resume_picker.resuming);
    assert_eq!(app.session.session_id.as_deref(), Some(SAVED));
    assert_eq!(
        messages(&app),
        [
            row("user", "Saved question"),
            row("command_status", "Resumed session `ession-1`")
        ]
    );
}

#[tokio::test]
async fn resuming_a_session_mid_turn_shows_its_loader() {
    let mut app = picking();
    resuming(&mut app);
    let mut state = session_state(
        SAVED,
        vec![json!({
            "id": "saved-wait",
            "type": "effect",
            "turnId": "turn-waiting",
            "generationStatus": "in_progress",
            "detail": {
                "toolName": "self.wait",
                "kind": "tool",
                "display": {"statusText": "Waiting for background work"},
            },
            "state": {"status": "running"},
        })],
    );
    state.session.status = serde_json::from_value(json!({
        "type": "running",
        "activeTurnId": "turn-waiting",
    }))
    .ok();
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: SAVED.into(),
            state,
        },
    );
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert_eq!(app.session.active_turn_id.as_deref(), Some("turn-waiting"));
    assert_eq!(app.view.loading.label(), "Waiting for background work");
}

#[tokio::test]
async fn resuming_a_mid_turn_session_over_a_generating_one_restarts_its_loader() {
    let mut app = picking_while_generating();
    app.view
        .loading
        .set_label("Running the old session's tests");
    let old_since = app.session.status;
    resuming(&mut app);
    let mut state = session_state(
        SAVED,
        vec![
            json!({
                "id": "earlier-call",
                "type": "effect",
                "turnId": "turn-earlier",
                "generationStatus": "in_progress",
                "detail": {"toolName": "file_system.bash", "kind": "tool",
                           "display": {"statusText": "Running tests"}},
                "state": {"status": "running"},
            }),
            message("saved-user", "user", "Saved question"),
        ],
    );
    state.session.status = serde_json::from_value(json!({
        "type": "running",
        "activeTurnId": "turn-streaming",
    }))
    .ok();
    std::thread::sleep(std::time::Duration::from_millis(2));
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: SAVED.into(),
            state,
        },
    );
    assert!(
        app.session.status != old_since,
        "the resumed turn keeps the old timer"
    );
    // The resumed turn runs no call: neither the old session's label nor the
    // call an earlier turn left unsettled names it.
    assert_ne!(app.view.loading.label(), "Running the old session's tests");
    assert_ne!(app.view.loading.label(), "Running tests");
}

#[tokio::test]
async fn a_resume_names_the_session_the_server_resumed() {
    let mut app = picking();
    resuming(&mut app);
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: SAVED.into(),
            state: session_state("imported-0a1b2c3d", vec![]),
        },
    );
    assert_eq!(
        messages(&app).last(),
        Some(&row("command_status", "Resumed session `0a1b2c3d`"))
    );
}

#[tokio::test]
async fn a_failed_resume_keeps_the_running_turn() {
    let mut app = picking_while_generating();
    let since = app.session.status;
    resuming(&mut app);
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Error(Severity::Error, "Failed to resume session.".into()),
    );
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert!(
        matches!((since, app.session.status), (Status::Generating { since: a }, Status::Generating { since: b }) if a == b)
    );
    assert!(app.queue.is_empty());
    assert_eq!(app.session.session_id.as_deref(), Some(CURRENT));
    assert!(!app.resume_picker.open);
    assert_eq!(
        messages(&app).last(),
        Some(&row("user", "Current question"))
    );
    assert_eq!(
        notice(&app),
        Some((Severity::Error, "Failed to resume session."))
    );
}

#[tokio::test]
async fn a_cancelled_resume_drops_its_late_answer_quietly() {
    for late in [
        Event::Error(Severity::Error, "Failed to resume session.".into()),
        Event::Resumed {
            id: SAVED.into(),
            state: saved_state(),
        },
    ] {
        let mut app = picking();
        resuming(&mut app);
        resume_picker::handle_key(&mut app, &stub(), KeyCode::Esc.into());
        assert!(!app.resume_picker.open);
        let cancelled = messages(&app);
        resume_picker::apply_event(&mut app, &stub(), late);
        assert_eq!(messages(&app), cancelled);
        assert_eq!(app.session.session_id.as_deref(), Some(CURRENT));
        assert!(app.overlays.notice.is_none());
    }
}

#[tokio::test]
async fn a_picker_resume_drops_the_post_init_notices_for_good() {
    let runtime = json!({"runtime": {"mcp": {"sources": [{"name": "weather", "kind": "server",
        "transport": "http", "status": "needs_auth", "tools": [], "error": null}]}}});
    let mut app = picking();
    vibe_rs::mcp::show_post_init_notices(&mut app, &runtime);
    app.session.runtime = runtime;
    let notices = |app: &App| {
        messages(app)
            .iter()
            .filter(|(_, text)| text.contains("needs OAuth authentication"))
            .count()
    };
    assert_eq!(notices(&app), 1);
    resuming(&mut app);
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: SAVED.into(),
            state: saved_state(),
        },
    );
    assert_eq!(app.session.session_id.as_deref(), Some(SAVED));
    assert_eq!(notices(&app), 0);
    assert!(app.overlays.notice.is_none());
}

#[tokio::test]
async fn a_failed_resume_over_a_preview_restores_the_current_transcript() {
    let mut app = picking();
    preview(&mut app);
    resuming(&mut app);
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Error(Severity::Error, "Failed to resume session.".into()),
    );
    assert_eq!(messages(&app), [row("user", "Current question")]);
    assert_eq!(
        notice(&app),
        Some((Severity::Error, "Failed to resume session."))
    );
}

#[tokio::test]
async fn a_failed_resume_without_a_preview_keeps_the_notices_shown_meanwhile() {
    let mut app = picking();
    app.view
        .transcript
        .add(&json!({"entry": {"id": "notice", "type": "message",
        "role": "command_result", "local": true, "generationStatus": "completed",
        "content": [{"type": "text", "text": "Shown while picking"}]}}));
    resuming(&mut app);
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Error(Severity::Error, "Failed to resume session.".into()),
    );
    assert_eq!(
        messages(&app),
        [
            row("user", "Current question"),
            row("command_result", "Shown while picking")
        ]
    );
    assert_eq!(
        notice(&app),
        Some((Severity::Error, "Failed to resume session."))
    );
}

#[tokio::test]
async fn a_picker_resume_adopts_the_resumed_sessions_paused_queue() {
    let mut app = picking();
    resuming(&mut app);
    let mut resumed = saved_state();
    resumed.turn_queue = serde_json::from_value(
        json!({"paused": true, "items": [{"id": "queue-1", "createdAt": 1,
        "entries": [{"entryId": "queued-1", "role": "user", "annotations": {},
            "content": [{"type": "text", "text": "Queued in the saved session"}]}]}]}),
    )
    .unwrap();
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: SAVED.into(),
            state: resumed,
        },
    );
    assert_eq!(app.queue.len(), 1);
    assert!(app.queue.paused);
    let texts: Vec<String> = messages(&app).into_iter().map(|(_, text)| text).collect();
    let saved = texts.iter().position(|text| text == "Saved question");
    let queued = texts
        .iter()
        .position(|text| text == "Queued in the saved session");
    assert!(
        saved < queued && saved.is_some(),
        "queued row after the history: {texts:?}"
    );
}

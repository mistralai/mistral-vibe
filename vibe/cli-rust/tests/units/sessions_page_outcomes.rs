//! What opening a session from the Sessions page leaves behind when it succeeds or fails.

use serde_json::json;
use vibe_rs::app::{App, Severity, Status};
use vibe_rs::server::PublicSessionState;
use vibe_rs::sessions_page::actions::{self, Event};
use vibe_rs::sessions_page::{self, Session, Trigger};

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

/// The page open on a saved session.
pub fn picking() -> App {
    let mut app = ready();
    sessions_page::open(&mut app, &stub(), Trigger::SessionsCommand);
    app.sessions_page.sessions = vec![Session {
        id: SAVED.into(),
        ..Session::default()
    }];
    sessions_page::relayout(&mut app);
    app
}

/// The page open while a server-started turn (e.g. a scheduled loop) runs.
pub fn picking_while_generating() -> App {
    let mut app = picking();
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    app
}

/// Enter was pressed and `session/resume` answered with `state`.
fn opened(app: &mut App, state: PublicSessionState) {
    app.sessions_page.busy = Some(SAVED.into());
    let ticket = app.sessions_page.ticket;
    actions::apply_event(app, &stub(), Event::Opened(ticket, Ok(Box::new(state))));
}

/// Enter was pressed and `session/resume` failed.
fn failed(app: &mut App, message: &str) {
    app.sessions_page.busy = Some(SAVED.into());
    let ticket = app.sessions_page.ticket;
    actions::apply_event(app, &stub(), Event::Opened(ticket, Err(message.into())));
}

#[tokio::test]
async fn a_successful_resume_retires_the_running_turn() {
    let mut app = picking_while_generating();
    opened(&mut app, saved_state());
    assert!(matches!(app.session.status, Status::Ready));
    assert!(app.queue.is_empty());
    assert!(!app.sessions_page.open);
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
    opened(&mut app, state);
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
    opened(&mut app, state);
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
    opened(&mut app, session_state("imported-0a1b2c3d", vec![]));
    assert_eq!(
        messages(&app).last(),
        Some(&row("command_status", "Resumed session `0a1b2c3d`"))
    );
}

#[tokio::test]
async fn a_failed_resume_keeps_the_running_turn() {
    let mut app = picking_while_generating();
    let since = app.session.status;
    failed(&mut app, "Failed to resume session.");
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert!(
        matches!((since, app.session.status), (Status::Generating { since: a }, Status::Generating { since: b }) if a == b)
    );
    assert!(app.queue.is_empty());
    assert_eq!(app.session.session_id.as_deref(), Some(CURRENT));
    assert!(app.sessions_page.open);
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
async fn an_open_drops_the_post_init_notices_for_good() {
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
    opened(&mut app, saved_state());
    assert_eq!(app.session.session_id.as_deref(), Some(SAVED));
    assert_eq!(notices(&app), 0);
    assert!(app.overlays.notice.is_none());
}

#[tokio::test]
async fn an_open_adopts_the_resumed_sessions_paused_queue() {
    let mut app = picking();
    let mut resumed = saved_state();
    resumed.turn_queue = serde_json::from_value(
        json!({"paused": true, "items": [{"id": "queue-1", "createdAt": 1,
        "entries": [{"entryId": "queued-1", "role": "user", "annotations": {},
            "content": [{"type": "text", "text": "Queued in the saved session"}]}]}]}),
    )
    .unwrap();
    opened(&mut app, resumed);
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

/// Enter on the highlighted saved session, in its own folder so no trust gate runs.
fn press_open(app: &mut App) {
    app.session.cwd = Some("/workspace".into());
    app.sessions_page.sessions[0].cwd = Some("/workspace".into());
    sessions_page::relayout(app);
    actions::open(app, &stub());
}

#[tokio::test]
async fn opening_leaves_the_page_at_once_with_a_loader() {
    let mut app = picking();
    press_open(&mut app);
    assert!(!app.sessions_page.open);
    assert!(sessions_page::switching(&app));
    assert!(messages(&app).is_empty());
    assert_eq!(app.view.loading.label(), "Opening session");
    opened(&mut app, saved_state());
    assert!(!sessions_page::switching(&app));
    assert!(!app.view.command_loading);
    assert_eq!(
        messages(&app),
        [
            row("user", "Saved question"),
            row("command_status", "Resumed session `ession-1`")
        ]
    );
}

#[tokio::test]
async fn a_failed_open_puts_the_conversation_back_under_the_page() {
    let mut app = picking();
    press_open(&mut app);
    failed(&mut app, "Failed to resume session.");
    assert!(app.sessions_page.open);
    assert!(!sessions_page::switching(&app));
    assert!(!app.view.command_loading);
    assert_eq!(messages(&app), [row("user", "Current question")]);
}

#[tokio::test]
async fn the_page_waits_for_a_session_still_opening() {
    let mut app = picking();
    press_open(&mut app);
    // The server goes on opening it: the page does not reopen over a session
    // the screen would no longer show.
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    assert!(!app.sessions_page.open);
    assert!(sessions_page::switching(&app));
    opened(&mut app, saved_state());
    assert!(app.is_session(SAVED));
}

#[tokio::test]
async fn hopping_between_sessions_keeps_one_resumed_row() {
    let mut app = picking();
    opened(&mut app, saved_state());
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    opened(
        &mut app,
        session_state(
            CURRENT,
            vec![message("current-user", "user", "Current question")],
        ),
    );
    let statuses: Vec<_> = messages(&app)
        .into_iter()
        .filter(|(role, _)| role == "command_status")
        .collect();
    assert_eq!(
        statuses,
        [row("command_status", "Resumed session `-session`")]
    );
}

#[tokio::test]
async fn opening_the_page_while_the_server_starts_waits_for_it() {
    let mut app = App::default();
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    assert!(app.sessions_page.awaiting_server);
    assert!(app.sessions_page.loading);
    app.set_status(Status::Ready);
    sessions_page::server_ready(&mut app, &stub());
    assert!(!app.sessions_page.awaiting_server);
}

#[tokio::test]
async fn an_opened_session_starts_with_no_folder_to_go_back_to() {
    let mut app = picking();
    app.session.cwd = Some("/work/current".into());
    app.session.agent_config.cwd = Some("/work/current".into());
    app.session.previous_cwd = Some("/work/earlier".into());
    let mut state = saved_state();
    state.session.cwd = Some("/work/saved".into());
    opened(&mut app, state);
    assert_eq!(app.session.cwd.as_deref(), Some("/work/saved"));
    // `/cd -` must not move the opened session into the folder of the one it replaced.
    assert_eq!(app.session.previous_cwd, None);
}

#[tokio::test]
async fn ctrl_c_and_esc_while_a_session_opens_wait_for_it() {
    let mut app = picking();
    press_open(&mut app);
    let quit = crate::support::send_key(&mut app, crate::support::ctrl('c'));
    assert!(
        !quit,
        "Ctrl+C neither quits nor arms quit while a session opens"
    );
    assert!(!app
        .quit
        .is_confirmed(vibe_rs::quit_manager::QuitConfirmKey::CtrlC));
    crate::support::send_key(
        &mut app,
        crate::support::key(crossterm::event::KeyCode::Esc),
    );
    // The server opens it whatever is pressed: the client follows it there.
    assert!(sessions_page::switching(&app));
    opened(&mut app, saved_state());
    assert!(app.is_session(SAVED));
}

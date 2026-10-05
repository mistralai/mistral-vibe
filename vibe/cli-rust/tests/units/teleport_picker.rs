//! The project picker opened for teleport, and stale saved-link recovery.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::teleport::{self, Reply};
use vibe_rs::vibe_code_project::{self as project, Event, Reply as ProjectReply, State};

use crate::teleport_support::{emit, rows, running, stub, view, view_json};

#[test]
fn stale_saved_project_recovers_into_the_picker() {
    let mut app = running();
    emit(
        &mut app,
        json!({"kind": "failed", "error": {"message": "gone", "code": "saved_project_stale"}}),
    );
    assert!(app.teleport.is_none());
    assert!(rows(&app).is_empty());

    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Recovered {
            session_id: "session".into(),
            picker_id: "picker".into(),
            prompt: Some("ship it".into()),
            message: "gone".into(),
            view: Some(Box::new(view(false))),
        },
    );
    assert!(app.vibe_code_project.open);
    assert!(app.vibe_code_project.teleport_pending);
    assert_eq!(
        app.vibe_code_project.teleport_prompt.as_deref(),
        Some("ship it")
    );
    assert_eq!(rows(&app)[0].1, teleport::STALE_PROJECT_MESSAGE);
}

#[test]
fn a_late_recover_leaves_a_newer_teleport_alone() {
    let mut app = running();
    let operation_id = app.teleport.as_ref().unwrap().operation_id.clone();
    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Recovered {
            session_id: "session".into(),
            picker_id: "picker".into(),
            prompt: None,
            message: "gone".into(),
            view: Some(Box::new(view(false))),
        },
    );
    assert!(!app.vibe_code_project.open);
    assert!(app
        .teleport
        .as_ref()
        .is_some_and(|op| op.operation_id == operation_id));
    assert_eq!(
        rows(&app)[1],
        ("command_error".into(), "gone".into(), false)
    );
}

#[test]
fn unrecovered_stale_project_shows_the_original_error() {
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Recovered {
            session_id: "session".into(),
            picker_id: "picker".into(),
            prompt: None,
            message: "gone".into(),
            view: None,
        },
    );
    assert!(!app.vibe_code_project.open);
    assert_eq!(rows(&app), [("command_error".into(), "gone".into(), false)]);
}

#[test]
fn recover_error_from_a_previous_session_is_dropped() {
    let mut app = App::default();
    app.session.session_id = Some("resumed".into());
    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Recovered {
            session_id: "session".into(),
            picker_id: "picker".into(),
            prompt: None,
            message: "gone".into(),
            view: None,
        },
    );
    assert!(rows(&app).is_empty());
}

fn opening(prompt: Option<&str>) -> App {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.session.session_id = Some("session".into());
    app.vibe_code_project = State {
        pending: true,
        session_id: "session".into(),
        teleport_pending: true,
        teleport_prompt: prompt.map(str::to_owned),
        ..State::default()
    };
    app
}

fn opened(app: &mut App, resolved: Option<&str>, cleared: bool) {
    let response = serde_json::from_value(json!({
        "pickerId": "picker",
        "view": view_json(cleared),
        "resolvedProjectId": resolved,
    }))
    .unwrap();
    project::apply_event(
        app,
        &stub(),
        ProjectReply {
            session_id: "session".into(),
            picker_id: String::new(),
            event: Event::Opened(response),
        },
    );
}

#[test]
fn a_resolved_saved_link_teleports_without_showing_the_picker() {
    let mut app = opening(Some("ship it"));
    opened(&mut app, Some("linked"), false);
    assert!(!app.vibe_code_project.open);
    let op = app.teleport.as_ref().expect("teleport started");
    assert_eq!(op.picker_id, "picker");
    assert_eq!(op.prompt.as_deref(), Some("ship it"));
    assert_eq!(
        rows(&app),
        [("teleport_status".into(), "Teleporting...".into(), true)]
    );
}

#[test]
fn an_unresolved_teleport_opens_the_picker_and_explains_a_changed_remote() {
    let mut app = opening(None);
    opened(&mut app, None, true);
    assert!(app.vibe_code_project.open);
    assert!(app.vibe_code_project.teleport_pending);
    assert!(app.teleport.is_none());
    assert_eq!(rows(&app)[0].1, project::teleport::REMOTE_CHANGED_MESSAGE);
}

fn selected(app: &mut App) {
    let response = serde_json::from_value(json!({
        "view": view_json(false),
        "project": {"projectId": "p1", "name": "Project"},
    }))
    .unwrap();
    project::apply_event(
        app,
        &stub(),
        ProjectReply {
            session_id: "session".into(),
            picker_id: "picker".into(),
            event: Event::Selected(response),
        },
    );
}

#[test]
fn escape_during_selection_does_not_start_the_teleport() {
    let mut app = opening(Some("ship it"));
    opened(&mut app, None, false);
    app.vibe_code_project.pending = true;
    app.vibe_code_project.cancel_requested = true;
    selected(&mut app);
    assert!(app.teleport.is_none());
    assert!(!rows(&app)
        .iter()
        .any(|(_, text, _)| text.starts_with("Linked")));
}

#[test]
fn selecting_a_project_continues_the_pending_teleport() {
    let mut app = opening(Some("ship it"));
    opened(&mut app, None, false);
    app.vibe_code_project.pending = true;
    selected(&mut app);
    assert!(!app.vibe_code_project.open);
    assert!(!app.vibe_code_project.teleport_pending);
    let op = app.teleport.as_ref().expect("teleport started");
    assert_eq!(op.picker_id, "picker");
    assert_eq!(op.prompt.as_deref(), Some("ship it"));
    assert!(!rows(&app)
        .iter()
        .any(|(_, text, _)| text.starts_with("Linked")));
}

#[test]
fn leaving_the_startup_resume_picker_drops_the_startup_teleport() {
    let mut app = App::default();
    app.session.teleport_on_start = true;
    app.resume_picker.open = true;
    vibe_rs::resume_picker::handle_key(&mut app, &stub(), KeyCode::Esc.into());
    assert!(!app.session.teleport_on_start);
}

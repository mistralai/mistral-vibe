//! Remote-project cancellation and abandoned-request recovery.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use std::sync::Arc;
use vibe_rs::app::{App, Status};
use vibe_rs::server::Client;
use vibe_rs::vibe_code_project::{self as project, Event, Reply, State};

fn pending() -> App {
    let mut app = App::default();
    app.session.session_id = Some("new-session".into());
    app.session.status = Status::Ready;
    app.vibe_code_project = State {
        pending: true,
        session_id: "old-session".into(),
        picker_id: "picker".into(),
        ..State::default()
    };
    app
}

#[test]
fn obsolete_session_reply_releases_its_pending_picker() {
    let mut app = pending();
    project::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        Reply {
            session_id: "old-session".into(),
            picker_id: "picker".into(),
            event: Event::Cancelled,
        },
    );
    assert!(!app.vibe_code_project.pending);
    assert!(!app.vibe_code_project.open);
}

#[test]
fn another_pickers_reply_does_not_settle_the_current_request() {
    let mut app = pending();
    project::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        Reply {
            session_id: "old-session".into(),
            picker_id: "other-picker".into(),
            event: Event::Cancelled,
        },
    );
    assert!(app.vibe_code_project.pending);
}

#[test]
fn failed_cancel_does_not_trap_the_user_in_the_panel() {
    let mut app = pending();
    app.vibe_code_project.open = true;
    app.vibe_code_project.session_id = "new-session".into();
    project::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        Reply {
            session_id: "new-session".into(),
            picker_id: "picker".into(),
            event: Event::CancelFailed("offline".into()),
        },
    );
    assert!(!app.vibe_code_project.open);
    assert!(!app.vibe_code_project.pending);
    assert!(!app.view.transcript.is_empty());
}

#[test]
fn same_picker_id_from_an_old_session_does_not_settle_a_new_request() {
    let mut app = pending();
    app.vibe_code_project.session_id = "new-session".into();
    project::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        Reply {
            session_id: "old-session".into(),
            picker_id: "picker".into(),
            event: Event::Cancelled,
        },
    );
    assert!(app.vibe_code_project.pending);
}

#[test]
fn escape_during_an_in_flight_request_is_retained() {
    let mut app = pending();
    project::input::handle_key(
        &mut app,
        &Arc::new(Client::stub()),
        crossterm::event::KeyCode::Esc.into(),
    );
    assert!(app.vibe_code_project.cancel_requested);
}

#[test]
fn replacing_the_session_closes_its_project_picker() {
    let mut app = pending();
    app.session.session_id = Some("old-session".into());
    app.vibe_code_project.open = true;
    app.vibe_code_project.pending = false;

    vibe_rs::commands::clear::apply_cleared(&mut app, "new-session".into(), None);

    assert_eq!(app.session.session_id.as_deref(), Some("new-session"));
    assert!(!app.vibe_code_project.open);
    assert!(app.vibe_code_project.session_id.is_empty());
}

#[tokio::test]
async fn stale_picker_does_not_rebind_to_a_replacement_session() {
    let mut app = pending();
    app.vibe_code_project.open = true;
    app.vibe_code_project.pending = false;
    let (tx, mut rx) = tokio::sync::mpsc::channel(1);
    app.command_tx = Some(tx);

    project::input::handle_key(
        &mut app,
        &Arc::new(Client::stub()),
        crossterm::event::KeyCode::Esc.into(),
    );

    assert!(!app.vibe_code_project.open);
    assert!(!app.vibe_code_project.pending);
    assert!(rx.try_recv().is_err());
}

#[tokio::test]
async fn opening_rejects_prompt_and_modal_submission_without_losing_input() {
    let (tx, _) = tokio::sync::mpsc::channel(1);
    for text in ["hello", "/theme"] {
        let mut app = pending();
        app.chat_input.load_full_text(text.into());
        vibe_rs::commands::submission::submit(&mut app, &Arc::new(Client::stub()), &tx);
        assert!(app.queue.is_empty());
        assert!(!app.theme_picker.open);
        assert_eq!(app.chat_input.full_text(), text);
    }
}

#[tokio::test]
async fn escape_while_opening_cancels_once_the_picker_id_arrives() {
    let mut app = pending();
    app.vibe_code_project.session_id = "new-session".into();
    app.vibe_code_project.cancel_requested = true;
    let (tx, mut rx) = tokio::sync::mpsc::channel(2);
    app.command_tx = Some(tx);
    let response = serde_json::from_value(serde_json::json!({
        "pickerId": "opened", "view": {"context": {"repoUrl": "repo", "repoName": "repo"}, "state": {"projects": []}, "git": {}}
    })).unwrap();
    let client = Arc::new(Client::stub());
    project::apply_event(
        &mut app,
        &client,
        Reply {
            session_id: "new-session".into(),
            picker_id: "picker".into(),
            event: Event::Opened(response),
        },
    );
    assert!(app.vibe_code_project.pending);
    assert_eq!(app.vibe_code_project.picker_id, "opened");
    let event = tokio::time::timeout(std::time::Duration::from_secs(1), rx.recv())
        .await
        .unwrap()
        .unwrap();
    let vibe_rs::commands::CommandEvent::RemoteProject(reply) = event else {
        panic!("expected project reply")
    };
    assert!(matches!(reply.event, Event::CancelFailed(_)));
    project::apply_event(&mut app, &client, *reply);
    assert!(!app.vibe_code_project.pending);
    assert!(!app.vibe_code_project.open);
}

#[test]
fn backtab_reaches_the_project_form() {
    let mut app = pending();
    app.vibe_code_project.pending = false;
    app.vibe_code_project.open = true;
    app.vibe_code_project.create = Some(project::Create {
        name: project::Field::new("name".into()),
        branch: project::Field::new("main".into()),
        branch_focused: false,
    });
    let client = Arc::new(Client::stub());
    let key = KeyEvent::new(KeyCode::BackTab, KeyModifiers::SHIFT);

    assert_eq!(
        vibe_rs::input::handle_priority_key(&mut app, &client, key),
        None
    );
    project::input::handle_key(&mut app, &client, key);

    assert!(app.vibe_code_project.create.unwrap().branch_focused);
}

#[tokio::test]
async fn failed_link_after_creation_keeps_the_create_form() {
    let mut app = pending();
    app.vibe_code_project.session_id = "new-session".into();
    app.vibe_code_project.open = true;
    app.vibe_code_project.create = Some(project::Create {
        name: project::Field::new("new".into()),
        branch: project::Field::new("main".into()),
        branch_focused: false,
    });
    let (tx, mut rx) = tokio::sync::mpsc::channel(2);
    app.command_tx = Some(tx);
    let response = serde_json::from_value(serde_json::json!({
        "project": {"projectId": "created", "name": "new", "repositories": [{"repoUrl": "repo"}]},
        "view": {
            "context": {"repoUrl": "repo", "repoName": "repo"},
            "state": {"projects": [{"projectId": "created", "name": "new", "repositories": [{"repoUrl": "repo"}]}]},
            "git": {}
        }
    }))
    .unwrap();
    let client = Arc::new(Client::stub());

    project::apply_event(
        &mut app,
        &client,
        Reply {
            session_id: "new-session".into(),
            picker_id: "picker".into(),
            event: Event::Created(response),
        },
    );

    assert_eq!(
        app.vibe_code_project
            .create
            .as_ref()
            .map(|create| create.name.text.as_str()),
        Some("new")
    );
    assert!(app.vibe_code_project.pending);
    let event = tokio::time::timeout(std::time::Duration::from_secs(1), rx.recv())
        .await
        .unwrap()
        .unwrap();
    let vibe_rs::commands::CommandEvent::RemoteProject(reply) = event else {
        panic!("expected project reply")
    };
    assert!(matches!(reply.event, Event::Error(_)));
    project::apply_event(&mut app, &client, *reply);
    assert_eq!(
        app.vibe_code_project
            .create
            .as_ref()
            .map(|create| create.name.text.as_str()),
        Some("new")
    );
}

#[tokio::test]
async fn back_during_creation_does_not_link_the_new_project() {
    let mut app = pending();
    app.vibe_code_project.session_id = "new-session".into();
    app.vibe_code_project.open = true;
    app.vibe_code_project.cancel_requested = true;
    app.vibe_code_project.query = project::Field::new("old search".into());
    let (tx, mut rx) = tokio::sync::mpsc::channel(2);
    app.command_tx = Some(tx);
    let response = serde_json::from_value(serde_json::json!({
        "project": {"projectId": "created", "name": "new"},
        "view": {"context": {"repoUrl": "repo", "repoName": "repo"}, "state": {"projects": []}, "git": {}}
    })).unwrap();
    project::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        Reply {
            session_id: "new-session".into(),
            picker_id: "picker".into(),
            event: Event::Created(response),
        },
    );
    assert!(app.vibe_code_project.open);
    assert!(app.vibe_code_project.create.is_none());
    assert!(app.vibe_code_project.query.text.is_empty());
    assert!(!app.vibe_code_project.pending);
    assert!(rx.try_recv().is_err());
}

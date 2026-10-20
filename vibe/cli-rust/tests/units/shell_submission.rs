use std::sync::Arc;

use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::config;
use vibe_rs::server::Client;

#[tokio::test]
async fn a_second_shell_command_during_startup_is_rejected_and_preserved() {
    let mut app = App::default();
    app.session.status = Status::Starting;
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.chat_input.load_full_text("!first".to_owned());
    submission::submit(&mut app, &client, &config_tx);
    app.chat_input.load_full_text("!second".to_owned());
    submission::submit(&mut app, &client, &config_tx);

    assert!(app.session.pending_shell.is_some());
    assert_eq!(app.chat_input.full_text(), "!second");
    assert_eq!(app.chat_input.cursor, 0);
    assert!(app
        .overlays
        .toasts
        .back()
        .is_some_and(|toast| toast.text.contains("pending or running")));
}

fn shell_running_app() -> App {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.status = Status::Ready;
    app.session.shell_operation_id = Some("shell-op".into());
    app
}

fn submit_text(app: &mut App, text: &str) -> bool {
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());
    app.chat_input.load_full_text(text.to_owned());
    submission::submit(app, &client, &config_tx)
}

fn last_toast(app: &App) -> Option<&str> {
    app.overlays.toasts.back().map(|toast| toast.text.as_str())
}

#[tokio::test]
async fn a_prompt_submitted_while_a_shell_command_runs_is_rejected_and_kept() {
    let mut app = shell_running_app();

    assert!(!submit_text(&mut app, "after shell"));

    assert_eq!(app.chat_input.full_text(), "after shell");
    assert!(app.queue.is_empty(), "nothing reaches the turn queue");
    assert!(matches!(app.session.status, Status::Ready));
    assert!(last_toast(&app).is_some_and(|text| text.contains("pending or running")));
}

#[tokio::test]
async fn exit_still_exits_while_a_shell_command_runs() {
    let mut app = shell_running_app();

    assert!(
        submit_text(&mut app, "/exit"),
        "the side-channel /exit exits"
    );

    assert!(
        last_toast(&app).is_none(),
        "no cannot-be-queued rejection is shown"
    );
}

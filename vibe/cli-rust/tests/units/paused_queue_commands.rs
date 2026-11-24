//! A paused queue lets idle-only commands such as `/retry` run, and holds back those that would drop it.

use std::sync::Arc;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::config;
use vibe_rs::server::Client;

use crate::feedback_support::notice_text;
use crate::support::{press, queued, settle};

/// An idle session whose last turn was interrupted, with a prompt held in the paused queue.
fn paused_session() -> (App, Arc<Client>, mpsc::UnboundedReceiver<Value>) {
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    app.session.status = Status::Ready;
    let (tx, _commands) = mpsc::channel(8);
    app.command_tx = Some(tx);
    app.session.can_retry = true;
    app.queue.items.push(queued("held", "held prompt"));
    app.queue.paused = true;
    let (client, frames) = Client::stub_answering();
    (app, Arc::new(client), frames)
}

fn submit(app: &mut App, client: &Arc<Client>, text: &str) {
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    app.chat_input.load_full_text(text.to_owned());
    assert!(!submission::submit(app, client, &config_tx));
}

async fn sent(frames: &mut mpsc::UnboundedReceiver<Value>) -> Vec<Value> {
    settle().await;
    std::iter::from_fn(|| frames.try_recv().ok()).collect()
}

#[tokio::test]
async fn retry_runs_while_the_queue_is_paused_and_keeps_it() {
    let (mut app, client, mut frames) = paused_session();

    submit(&mut app, &client, "/retry");

    assert_eq!(notice_text(&app), None);
    assert!(app.chat_input.full_text().is_empty());
    let frames = sent(&mut frames).await;
    let start = frames
        .iter()
        .find(|frame| frame["method"] == "turn/start")
        .expect("the retry starts a turn");
    assert_eq!(start["params"]["injected"], true);
    assert!(
        !frames
            .iter()
            .any(|frame| frame["method"] == "session/turn/queue/resume"),
        "the queue stays held"
    );
    assert!(app.queue.paused);
    assert_eq!(app.queue.len(), 1);
}

#[tokio::test]
async fn idle_commands_run_while_the_queue_is_paused() {
    let (mut app, client, _frames) = paused_session();

    submit(&mut app, &client, "/model");

    assert_eq!(notice_text(&app), None);
    assert!(app.model_picker.open);
}

#[tokio::test]
async fn commands_that_drop_the_queue_wait_for_it() {
    for command in ["/clear", "/compact", "/rewind", "/resume"] {
        let (mut app, client, mut frames) = paused_session();

        submit(&mut app, &client, command);

        assert_eq!(app.chat_input.full_text(), command);
        assert_eq!(
            notice_text(&app),
            Some("Slash commands cannot be queued — send the held prompts with Enter or remove them first."),
            "{command}"
        );
        assert!(sent(&mut frames).await.is_empty(), "{command}");
    }
}

#[tokio::test]
async fn shell_commands_still_wait_for_a_paused_queue() {
    let (mut app, client, mut frames) = paused_session();

    submit(&mut app, &client, "!ls");

    assert_eq!(app.chat_input.full_text(), "!ls");
    assert!(
        notice_text(&app).is_some_and(|text| text.starts_with("Shell commands cannot be queued"))
    );
    assert!(sent(&mut frames).await.is_empty());
}

#[tokio::test]
async fn a_running_turn_still_holds_back_idle_commands() {
    let (mut app, client, _frames) = paused_session();
    app.session.status = Status::Generating {
        since: vibe_rs::clock::now(),
    };

    submit(&mut app, &client, "/retry");

    assert_eq!(app.chat_input.full_text(), "/retry");
    assert_eq!(
        notice_text(&app),
        Some("Slash commands cannot be queued — wait for the current job to finish.")
    );
}

#[tokio::test]
async fn double_escape_does_not_open_rewind_on_a_paused_queue() {
    let (mut app, _client, _frames) = paused_session();
    app.view
        .transcript
        .add(&json!({"entry": crate::support::message("u1", "user", "first")}));

    press(&mut app, KeyCode::Esc);
    press(&mut app, KeyCode::Esc);

    assert_eq!(app.rewind.entry_id, None);
    assert_eq!(
        notice_text(&app),
        Some("Rewind is not available — send the held prompts with Enter or remove them first.")
    );
    assert!(app.queue.paused);
}

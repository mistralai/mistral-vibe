//! The bare `/rename` usage tip must render literally, not through Markdown.
use std::sync::Arc;

use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::{submission, CommandEvent};
use vibe_rs::config;
use vibe_rs::server::Client;

#[tokio::test]
async fn a_missing_title_reports_a_usage_error() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.session.session_id = Some("session-1".to_owned());
    let (tx, mut rx) = mpsc::channel(4);
    app.command_tx = Some(tx);
    app.chat_input.load_full_text("/rename".to_owned());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    submission::submit(&mut app, &client, &config_tx);

    let CommandEvent::Error(text) = rx.recv().await.expect("usage error") else {
        panic!("expected the usage error, not markdown-swallowed output");
    };
    assert_eq!(text, "Usage: /rename <title>");
}

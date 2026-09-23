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

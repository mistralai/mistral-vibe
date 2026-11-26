//! Ready-event cache writes use an isolated Vibe home.

use std::sync::Arc;

use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::CommandEvent;
use vibe_rs::config;
use vibe_rs::event_handler::apply_startup_event;
use vibe_rs::server::Client;
use vibe_rs::startup::{Attach, ConfigRead, Ready, StartupEvent};
use vibe_rs::utils::startup_cache::StartupConfig;

#[tokio::test]
async fn apply_ready_event_drives_flush_pending() {
    let home = tempfile::tempdir().unwrap();
    std::env::set_var("VIBE_HOME", home.path());
    let mut app = App::default();
    app.session.status = Status::Starting;
    app.pending_commands.push("/help".to_owned());
    let before = app.view.transcript.revision();

    let (command_tx, _command_rx) = mpsc::channel::<CommandEvent>(8);
    app.command_tx = Some(command_tx);
    app.session.session_id = Some("rs-test".to_owned());

    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());
    let ready = Ready {
        startup_cache: StartupConfig::default(),
        config: ConfigRead::default(),
        state: serde_json::json!({
            "eventId": 0,
            "session": {"id": "rs-test"},
        }),
        attach: Attach::Started,
        runtime: serde_json::json!({"runtime": {}}),
        is_cold_start: true,
        settled: None,
        prepared: None,
        absorbed: Vec::new(),
    };

    apply_startup_event(
        &mut app,
        &client,
        &config_tx,
        StartupEvent::Ready(Box::new(ready)),
    );

    assert!(matches!(app.session.status, Status::Ready));
    assert!(app.pending_commands.is_empty());
    assert!(app.view.transcript.revision() > before);
    assert!(home.path().join("ui_startup_config.json").exists());
}

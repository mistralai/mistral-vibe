//! The home-folder warning on a cold start: mounted by the unready config, settled by Ready.

use std::sync::Arc;

use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::config;
use vibe_rs::event_handler::apply_startup_event;
use vibe_rs::server::{Client, HistoryEntry, MessageContent};
use vibe_rs::startup::{Attach, ConfigRead, Ready, StartupEvent};
use vibe_rs::utils::startup_cache::StartupConfig;

/// Starts from a fresh VIBE_HOME with HOME and the cwd on the same temp folder.
fn in_home_folder() -> (tempfile::TempDir, tempfile::TempDir) {
    let vibe_home = tempfile::tempdir().unwrap();
    let home = tempfile::tempdir().unwrap();
    let path = home.path().canonicalize().unwrap();
    std::env::set_var("VIBE_HOME", vibe_home.path());
    std::env::set_var("HOME", &path);
    std::env::set_current_dir(&path).unwrap();
    (vibe_home, home)
}

fn ready(show_warning: bool) -> StartupEvent {
    StartupEvent::Ready(Box::new(Ready {
        startup_cache: StartupConfig {
            show_dangerous_directory_warning: show_warning,
            ..StartupConfig::default()
        },
        config: ConfigRead::default(),
        state: json!({"eventId": 1, "session": {"id": "cold"}, "history": []}),
        attach: Attach::Started,
        runtime: json!({"runtime": {}}),
        is_cold_start: true,
        settled: None,
        prepared: None,
        absorbed: Vec::new(),
    }))
}

fn home_warnings(app: &App) -> usize {
    app.view
        .transcript
        .lines()
        .filter(|line| match &line.entry {
            HistoryEntry::Message(message) => {
                message.role == "warning"
                    && message.content.iter().any(|block| {
                        matches!(block, MessageContent::Text { text } if text.contains("You are in the home directory"))
                    })
            }
            _ => false,
        })
        .count()
}

/// The cold start's unready config, then Ready with `show_warning`.
fn cold_start(show_warning: bool) -> (usize, usize) {
    let _dirs = in_home_folder();
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = App::default();
    let unready = StartupEvent::Config(Box::default());
    apply_startup_event(&mut app, &client, &config_tx, unready);
    let before_ready = home_warnings(&app);
    apply_startup_event(&mut app, &client, &config_tx, ready(show_warning));
    (before_ready, home_warnings(&app))
}

#[tokio::test]
async fn the_unready_config_mounts_the_home_warning_once() {
    assert_eq!(cold_start(true), (1, 1));
}

#[tokio::test]
async fn a_ready_config_that_turns_it_off_removes_the_home_warning() {
    assert_eq!(cold_start(false), (1, 0));
}

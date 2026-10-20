//! Startup before the first Ready: held prompts and the dangerous-folder warning.

// The env guard must span the awaits that touch VIBE_HOME; each test owns its runtime.
#![allow(clippy::await_holding_lock)]

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::cli::StartupResume;
use vibe_rs::commands::submission;
use vibe_rs::config;
use vibe_rs::event_handler::{apply_startup_event, show_dangerous_directory_warning};
use vibe_rs::resume_picker;
use vibe_rs::server::{Client, HistoryEntry, MessageContent};
use vibe_rs::startup::{Attach, ConfigRead, Ready, StartupEvent};
use vibe_rs::utils::startup_cache::StartupConfig;

const SAVED: &str = "saved-session";

/// Guards VIBE_HOME, HOME, the cwd and the theme, which are process-global.
static ENV_LOCK: Mutex<()> = Mutex::new(());

fn isolated_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "vibe-rs-startup-ready-{}-{name}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::env::set_var("VIBE_HOME", &dir);
    dir
}

fn message(id: &str, role: &str, text: &str) -> Value {
    json!({"id": id, "type": "message", "role": role, "generationStatus": "completed",
        "content": [{"type": "text", "text": text}]})
}

fn ready(session: &str, history: Vec<Value>, attach: Attach) -> StartupEvent {
    StartupEvent::Ready(Box::new(Ready {
        startup_cache: StartupConfig::default(),
        config: ConfigRead::default(),
        state: json!({"eventId": 1, "session": {"id": session}, "history": history}),
        attach,
        runtime: json!({"runtime": {}}),
        is_cold_start: true,
        settled: None,
        prepared: None,
        absorbed: Vec::new(),
    }))
}

fn rows(app: &App) -> Vec<(String, String)> {
    app.view
        .transcript
        .lines()
        .filter_map(|entry| match entry.entry {
            HistoryEntry::Message(message) => Some((
                message.role.clone(),
                message
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        MessageContent::Text { text } => Some(text.as_str()),
                        _ => None,
                    })
                    .collect(),
            )),
            _ => None,
        })
        .collect()
}

/// Types a prompt before the session is ready, then attaches `session` and readies it.
async fn held_prompt_reaches(resume: StartupResume, session: &str, attach: Attach) {
    let _lock = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
    let home = isolated_dir("held-prompt");
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = App::default();
    app.session.startup_resume = resume;
    app.chat_input.input = "go".into();
    submission::submit(&mut app, &client, &config_tx);
    assert_eq!(app.queue.len(), 1);
    assert!(!app.queue.items[0].sent, "the prompt waits for the session");
    assert!(matches!(app.session.status, Status::Starting));

    let attached = StartupEvent::Attached(session.into());
    apply_startup_event(&mut app, &client, &config_tx, attached);
    assert!(
        !app.queue.items[0].sent,
        "attaching alone does not dispatch"
    );
    apply_startup_event(
        &mut app,
        &client,
        &config_tx,
        ready(session, Vec::new(), attach),
    );
    assert_eq!(app.queue.len(), 1, "the held prompt is dispatched once");
    assert!(app.queue.items[0].sent);
    assert!(matches!(app.session.status, Status::Generating { .. }));

    let enqueue = loop {
        let frame = tokio::time::timeout(std::time::Duration::from_secs(5), frames.recv())
            .await
            .expect("the prompt reaches the wire in time")
            .expect("the prompt reaches the wire");
        if frame["method"] == "session/turn/enqueue" {
            break frame;
        }
    };
    assert_eq!(enqueue["params"]["sessionId"], session);
    assert_eq!(enqueue["params"]["entries"][0]["content"][0]["text"], "go");
    let _ = std::fs::remove_dir_all(home);
}

#[tokio::test]
async fn a_prompt_typed_during_a_fresh_start_is_sent_once_the_session_is_ready() {
    held_prompt_reaches(StartupResume::None, "fresh-session", Attach::Started).await;
}

#[tokio::test]
async fn a_prompt_typed_while_resume_by_id_attaches_goes_to_the_resumed_session() {
    let resume = StartupResume::ById(SAVED.into());
    held_prompt_reaches(resume, SAVED, Attach::Resumed(SAVED.into())).await;
}

#[tokio::test]
async fn resuming_in_the_home_folder_keeps_both_the_warning_and_the_history() {
    let _lock = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
    let vibe_home = isolated_dir("dangerous-vibe-home");
    let home =
        std::env::temp_dir().join(format!("vibe-rs-startup-ready-{}-home", std::process::id()));
    std::fs::create_dir_all(&home).unwrap();
    let home = home.canonicalize().unwrap();
    let previous_cwd = std::env::current_dir().unwrap();
    let previous_home = std::env::var_os("HOME");
    std::env::set_var("HOME", &home);
    std::env::set_current_dir(&home).unwrap();

    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = App::default();
    show_dangerous_directory_warning(&mut app);
    let history = vec![
        message("previous-user", "user", "Hello from a previous run"),
        message("previous-assistant", "assistant", "Welcome back!"),
    ];
    let resumed = ready(SAVED, history, Attach::Resumed(SAVED.into()));
    apply_startup_event(&mut app, &client, &config_tx, resumed);

    std::env::set_current_dir(previous_cwd).unwrap();
    match previous_home {
        Some(value) => std::env::set_var("HOME", value),
        None => std::env::remove_var("HOME"),
    }
    let rows = rows(&app);
    let position = |role: &str, needle: &str| {
        rows.iter()
            .position(|(r, text)| r == role && text.contains(needle))
            .unwrap_or_else(|| panic!("missing {role} {needle:?} in {rows:?}"))
    };
    assert!(position("user", "Hello from a previous run") < position("assistant", "Welcome back!"));
    position("warning", "You are in the home directory");
    let _ = std::fs::remove_dir_all(home);
    let _ = std::fs::remove_dir_all(vibe_home);
}

#[tokio::test]
async fn a_picker_resume_before_ready_keeps_the_adopted_paused_queue() {
    let _lock = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
    let home = isolated_dir("picker-before-ready");
    let client = Arc::new(Client::stub_answering().0);
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = App::default();
    app.session.startup_resume = StartupResume::Picker;
    apply_startup_event(
        &mut app,
        &client,
        &config_tx,
        StartupEvent::Attached("fresh".into()),
    );
    resume_picker::open(&mut app, &client);
    app.commit_started();
    app.resume_picker.resuming = true;
    let saved = json!({"eventId": 1, "session": {"id": SAVED}, "history": [],
        "turnQueue": {"paused": true, "items": [{"id": "queue-1", "createdAt": 1,
            "entries": [{"entryId": "queued-1", "role": "user", "annotations": {},
                "content": [{"type": "text", "text": "Queued in the saved session"}]}]}]}});
    let state = serde_json::from_value(saved).unwrap();
    let resumed = resume_picker::Event::Resumed {
        id: SAVED.into(),
        state,
    };
    resume_picker::apply_event(&mut app, &client, resumed);
    assert!(app.queue.paused && app.queue.len() == 1);

    let mut fresh = ready("fresh", Vec::new(), Attach::Started);
    if let StartupEvent::Ready(ready) = &mut fresh {
        ready.state["turnQueue"] = json!({"paused": false, "items": []});
    }
    apply_startup_event(&mut app, &client, &config_tx, fresh);

    assert_eq!(app.session.session_id.as_deref(), Some(SAVED));
    assert_eq!(app.queue.len(), 1);
    assert!(
        app.queue.paused,
        "the discarded fresh session's queue must not unpause it"
    );
    let _ = std::fs::remove_dir_all(home);
}

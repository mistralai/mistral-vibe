//! Slash commands submitted during `Status::Starting` are deferred and replayed
//! once `apply_ready` settles, matching Python's `_session_ready.wait()` gate.

use std::sync::Arc;

use tokio::sync::mpsc;

use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::commands::CommandEvent;
use vibe_rs::server::Client;
use vibe_rs::{completion_manager, config};

/// A `/whoami` submitted while the session is still starting is stored in
/// `pending_commands` instead of firing RPCs against an unready session.
#[tokio::test]
async fn slash_command_during_starting_is_deferred() {
    let mut app = App::default();
    app.session.status = Status::Starting;
    app.chat_input.load_full_text("/whoami".to_owned());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    submission::submit(&mut app, &client, &config_tx);

    assert_eq!(app.pending_commands.len(), 1);
    assert_eq!(app.pending_commands[0], "/whoami");
    assert!(app.chat_input.input.is_empty(), "input should be cleared");
}

/// Multiple commands during `Starting` are deferred in order.
#[tokio::test]
async fn multiple_commands_during_starting_are_deferred_in_order() {
    let mut app = App::default();
    app.session.status = Status::Starting;
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.chat_input.load_full_text("/help".to_owned());
    submission::submit(&mut app, &client, &config_tx);
    app.chat_input.load_full_text("/status".to_owned());
    submission::submit(&mut app, &client, &config_tx);

    assert_eq!(app.pending_commands.len(), 2);
    assert_eq!(app.pending_commands[0], "/help");
    assert_eq!(app.pending_commands[1], "/status");
}

/// A regular prompt (not a slash command) during `Starting` goes through the
/// message queue, not the pending-commands list.
#[tokio::test]
async fn regular_prompt_during_starting_is_not_deferred() {
    let mut app = App::default();
    app.session.status = Status::Starting;
    app.chat_input.input = "hello world".to_owned();
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    submission::submit(&mut app, &client, &config_tx);

    assert!(app.pending_commands.is_empty(), "prompts are not deferred");
    assert!(!app.queue.is_empty(), "prompt should be queued");
}

/// Commands are not deferred when the session is already `Ready`.
#[tokio::test]
async fn command_during_ready_is_not_deferred() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.chat_input.load_full_text("/help".to_owned());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    submission::submit(&mut app, &client, &config_tx);

    assert!(
        app.pending_commands.is_empty(),
        "command should not be deferred when Ready"
    );
}

/// `flush_pending` clears the pending list.
#[tokio::test]
async fn flush_pending_clears_deferred_commands() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.pending_commands.push("/help".to_owned());
    app.pending_commands.push("/status".to_owned());

    submission::flush_pending(&mut app, &client, &config_tx);

    assert!(
        app.pending_commands.is_empty(),
        "pending commands should be flushed"
    );
}

/// `flush_pending` actually executes the deferred commands: `/help` adds its
/// output to the transcript, proving the replay path works end-to-end.
#[tokio::test]
async fn flush_pending_executes_deferred_help() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.pending_commands.push("/help".to_owned());
    let before = app.view.transcript.revision();
    submission::flush_pending(&mut app, &client, &config_tx);

    assert!(
        app.pending_commands.is_empty(),
        "pending commands should be flushed"
    );
    assert!(
        app.view.transcript.revision() > before,
        "the deferred /help should have added entries to the transcript"
    );
}

/// Side-channel commands (e.g. `/help`) are deferred during `Starting` too —
/// the gate covers every slash command, not just the non-side-channel ones,
/// matching Python's `_dispatch_idle_input` which awaits `_session_ready`
/// before classification.
#[tokio::test]
async fn side_channel_command_during_starting_is_deferred() {
    let mut app = App::default();
    app.session.status = Status::Starting;
    app.chat_input.load_full_text("/help".to_owned());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    submission::submit(&mut app, &client, &config_tx);

    assert_eq!(app.pending_commands.len(), 1);
    assert_eq!(app.pending_commands[0], "/help");
    assert!(app.chat_input.input.is_empty(), "input should be cleared");
}

/// `apply_startup_event(StartupEvent::Ready)` drives `flush_pending` end to
/// end: a command deferred earlier is replayed once the Ready event lands, so
/// the `event_handler` → `submission::flush_pending` wiring is exercised, not
/// just the isolated function.
#[tokio::test]
async fn apply_ready_event_drives_flush_pending() {
    use vibe_rs::event_handler::apply_startup_event;
    use vibe_rs::startup::{Attach, ConfigRead, Ready, StartupEvent};
    use vibe_rs::utils::startup_cache::StartupConfig;

    let mut app = App::default();
    app.session.status = Status::Starting;
    app.pending_commands.push("/help".to_owned());
    let before = app.view.transcript.revision();

    // `submit` needs the channel for the deferred-push path; here the command is
    // already pending, so only the Ready event's flush matters. A command_tx is
    // not required for `/help` (no RPC), but the session id must be set so the
    // reducer does not bail out of `apply_ready`.
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
    };
    apply_startup_event(
        &mut app,
        &client,
        &config_tx,
        StartupEvent::Ready(Box::new(ready)),
    );

    assert!(matches!(app.session.status, Status::Ready));
    assert!(
        app.pending_commands.is_empty(),
        "Ready event should flush pending commands"
    );
    assert!(
        app.view.transcript.revision() > before,
        "deferred /help should have added transcript entries via the Ready event"
    );
}

/// A deferred RPC command replayed against a stub client fails gracefully: the
/// background read returns `None` and the transcript records the user's command
/// line, with no panic and no spinner imbalance left behind.
#[tokio::test]
async fn flush_pending_replay_against_stub_does_not_panic() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.session.session_id = Some("rs-test".to_owned());
    let (command_tx, _command_rx) = mpsc::channel::<CommandEvent>(8);
    app.command_tx = Some(command_tx);
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.pending_commands.push("/whoami".to_owned());
    let before = app.view.transcript.revision();
    submission::flush_pending(&mut app, &client, &config_tx);

    assert!(
        app.pending_commands.is_empty(),
        "pending commands should be flushed"
    );
    // The user's command line is recorded synchronously by `run_command`, even
    // though the background identity read will fail against the stub.
    assert!(
        app.view.transcript.revision() > before,
        "the deferred /whoami command line should be recorded"
    );
}

/// A deferred `/exit` replays through `flush_pending` which must return true so
/// the event loop quits (Bugbot: deferred exit command never quits).
#[tokio::test]
async fn flush_pending_propagates_deferred_exit() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.pending_commands.push("/exit".to_owned());
    let exit = submission::flush_pending(&mut app, &client, &config_tx);

    assert!(exit, "deferred /exit should signal quit");
    assert!(app.pending_commands.is_empty());
}

/// A busy reject preserves a newer draft while later side-channel commands run.
#[tokio::test]
async fn flush_pending_preserves_in_progress_input() {
    let mut app = App::default();
    app.session.status = Status::Generating {
        since: std::time::Instant::now(),
    };
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.pending_commands.push("/clear".to_owned());
    app.pending_commands.push("/help".to_owned());
    app.chat_input.input = "unfinished thought".to_owned();
    let before = app.view.transcript.revision();
    let exit = submission::flush_pending(&mut app, &client, &config_tx);

    assert!(!exit);
    assert_eq!(app.chat_input.input, "unfinished thought");
    assert!(app.view.transcript.revision() > before);
}

/// A busy reject restores the command, reopens completions, and keeps replaying.
#[tokio::test]
async fn flush_pending_rejects_non_side_channel_while_generating() {
    let mut app = App::default();
    app.session.status = Status::Generating {
        since: std::time::Instant::now(),
    };
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.pending_commands.push("/clear".to_owned());
    app.pending_commands.push("/help".to_owned());
    let before = app.view.transcript.revision();
    let exit = submission::flush_pending(&mut app, &client, &config_tx);

    assert!(!exit);
    assert_eq!(app.chat_input.full_text(), "/clear");
    assert_eq!(app.chat_input.cursor, 0);
    assert!(completion_manager::is_open(&app));
    assert!(app.view.transcript.revision() > before);
}

/// `flush_pending` with nothing deferred keeps a dismissed completion popup
/// closed: a Ready event alone must not reset the user's completion state
/// (Bugbot: ready resets open completions).
#[tokio::test]
async fn flush_pending_without_commands_preserves_dismissed_completions() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let client = Arc::new(Client::stub());

    app.chat_input.load_full_text("/he".to_owned());
    completion_manager::input_changed(&mut app);
    assert!(completion_manager::is_open(&app), "popup should be open");
    assert!(completion_manager::dismiss(&mut app));
    assert!(
        !completion_manager::is_open(&app),
        "popup should be dismissed"
    );

    submission::flush_pending(&mut app, &client, &config_tx);

    assert!(
        !completion_manager::is_open(&app),
        "a Ready with no deferred commands must not reopen the dismissed popup"
    );
    assert_eq!(app.chat_input.full_text(), "/he");
    assert!(app.pending_commands.is_empty());
}

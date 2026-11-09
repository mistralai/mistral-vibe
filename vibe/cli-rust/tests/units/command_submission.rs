//! Which submitted inputs exit, get refused while busy, or show a loading line, and what reaches the wire.

use std::sync::Arc;

use crossterm::event::KeyCode;
use serde_json::Value;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::{branch, submission, CommandEvent};
use vibe_rs::config;
use vibe_rs::focus::Focus;
use vibe_rs::post_ready::AccountReads;
use vibe_rs::server::{Client, HistoryEntry, MessageContent, RpcError};

use crate::feedback_support::notice_text;
use crate::support::{key, settle};

struct Harness {
    app: App,
    client: Arc<Client>,
    frames: mpsc::UnboundedReceiver<Value>,
    commands: mpsc::Receiver<CommandEvent>,
}

impl Harness {
    /// An idle session whose client records every frame it writes.
    fn ready() -> Self {
        let mut app = App::default();
        app.session.session_id = Some("session".into());
        app.session.status = Status::Ready;
        let (tx, commands) = mpsc::channel(8);
        app.command_tx = Some(tx);
        let (client, frames) = Client::stub_answering();
        Self {
            app,
            client: Arc::new(client),
            frames,
            commands,
        }
    }

    fn submit(&mut self, text: &str) -> bool {
        let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
        self.app.chat_input.load_full_text(text.to_owned());
        submission::submit(&mut self.app, &self.client, &config_tx)
    }

    /// Methods written so far, once spawned requests had a chance to run.
    async fn sent_methods(&mut self) -> Vec<String> {
        settle().await;
        std::iter::from_fn(|| self.frames.try_recv().ok())
            .filter_map(|frame| frame["method"].as_str().map(str::to_owned))
            .collect()
    }

    fn notice(&self) -> Option<&str> {
        notice_text(&self.app)
    }
}

#[tokio::test]
async fn every_exit_alias_exits_without_starting_a_turn() {
    for alias in ["/exit", "exit", "quit", ":q", ":quit"] {
        let mut harness = Harness::ready();

        assert!(harness.submit(alias), "{alias:?} exits");

        assert!(harness.app.queue.is_empty(), "{alias:?} is not queued");
        assert!(matches!(harness.app.session.status, Status::Ready));
        assert_eq!(harness.sent_methods().await, Vec::<String>::new());
    }
}

#[tokio::test]
async fn the_fork_alias_branches_the_session_like_branch() {
    let mut harness = Harness::ready();

    assert!(!harness.submit("/fork"));

    let sent = harness.sent_methods().await;
    assert!(sent.contains(&"session/fork".to_owned()), "{sent:?}");
    let echoed = harness
        .app
        .view
        .transcript
        .lines()
        .any(|line| match line.entry {
            HistoryEntry::Message(message) => message.content.iter().any(|block| match block {
                MessageContent::Text { text } => text.contains("/fork"),
                _ => false,
            }),
            _ => false,
        });
    assert!(!echoed, "slash commands are not echoed");
}

#[tokio::test]
async fn branch_forks_at_once_while_busy_but_waits_for_a_running_job() {
    let busy_states: [fn(&mut App); 3] = [
        |app| {
            app.session.active_turn_id = Some("turn".into());
            app.session.status = Status::Generating {
                since: vibe_rs::clock::now(),
            };
        },
        |app| {
            app.session.status = Status::Generating {
                since: vibe_rs::clock::now(),
            };
        },
        |app| app.queue.paused = true,
    ];
    for make_busy in busy_states {
        let mut harness = Harness::ready();
        make_busy(&mut harness.app);

        assert!(!harness.submit("/branch"));

        assert!(harness.app.chat_input.full_text().is_empty());
        assert_eq!(harness.notice(), None);
        assert!(harness
            .sent_methods()
            .await
            .contains(&"session/fork".to_owned()));
    }
    let mut harness = Harness::ready();
    harness.app.compacting = true;

    assert!(!harness.submit("/branch"));

    assert_eq!(harness.app.chat_input.full_text(), "/branch");
    assert!(!harness
        .sent_methods()
        .await
        .contains(&"session/fork".to_owned()));
}

#[test]
fn a_branch_opens_the_resume_command_and_adds_no_row() {
    let statuses = [
        Status::Ready,
        Status::Generating {
            since: vibe_rs::clock::now(),
        },
    ];
    for status in statuses {
        let mut app = App::default();
        app.session.status = status;

        branch::apply_branched(&mut app, "session", "copy-id", Some("unified"));
        app.set_status(Status::Ready);

        assert_eq!(app.focus(), Focus::BranchApp);
        assert_eq!(app.branch_app.session_id, "copy-id");
        assert_eq!(app.view.transcript.lines().count(), 0);
        assert_eq!(notice_text(&app), None);
        vibe_rs::branch_app::handle_key(&mut app, key(KeyCode::Esc));
        assert_eq!(app.focus(), Focus::Composer);
    }
}

#[test]
fn a_refused_busy_branch_says_to_retry_once_the_response_ends() {
    let error = |harness_code: &str| -> RpcError {
        serde_json::from_value(serde_json::json!({
            "code": "conflict",
            "message": "server message",
            "data": {"harnessCode": harness_code},
        }))
        .expect("error parses")
    };

    assert_eq!(
        branch::branch_failure_message(&error("fork_entry_required")),
        "Can't branch while this response is running. Try again when it finishes."
    );
    assert_eq!(
        branch::branch_failure_message(&error("session_busy")),
        "Failed to branch session: server message"
    );
}

#[tokio::test]
async fn display_settings_run_during_a_turn_and_with_a_paused_queue() {
    let busy_states: [fn(&mut App); 2] = [
        |app| {
            app.session.active_turn_id = Some("turn".into());
            app.session.status = Status::Generating {
                since: vibe_rs::clock::now(),
            };
        },
        |app| app.queue.paused = true,
    ];
    for make_busy in busy_states {
        for command in ["/theme", "/log-level", "/voice", "/proxy-setup"] {
            let mut harness = Harness::ready();
            make_busy(&mut harness.app);

            assert!(!harness.submit(command));

            assert!(harness.app.chat_input.full_text().is_empty(), "{command}");
            assert_eq!(harness.notice(), None, "{command} is not refused");
        }
        // The harness refuses MCP, connector and plugin changes during a turn.
        for command in ["/mcp", "/connectors", "/plugins", "/reload-plugins"] {
            let mut harness = Harness::ready();
            make_busy(&mut harness.app);

            assert!(!harness.submit(command));

            assert_eq!(harness.app.chat_input.full_text(), command);
            assert!(harness
                .notice()
                .is_some_and(|text| text.starts_with("Slash commands cannot be queued")));
        }
        let mut harness = Harness::ready();
        make_busy(&mut harness.app);
        for (command, open) in [
            ("/theme", (|app| app.theme_picker.open) as fn(&App) -> bool),
            ("/log-level", |app| app.log_level_picker.open),
            ("/voice", |app| app.voice_app.open),
        ] {
            assert!(!harness.submit(command));
            assert!(open(&harness.app), "{command} opens its picker");
            harness.app.theme_picker.open = false;
            harness.app.log_level_picker.open = false;
            harness.app.voice_app.open = false;
        }
    }
}

#[tokio::test]
async fn settings_wait_for_a_running_job_while_read_only_commands_still_run() {
    let jobs: [fn(&mut App); 2] = [
        |app| app.compacting = true,
        |app| app.agents.installing = true,
    ];
    for start_job in jobs {
        for command in ["/theme", "/proxy-setup"] {
            let mut harness = Harness::ready();
            start_job(&mut harness.app);

            assert!(!harness.submit(command));

            assert_eq!(harness.app.chat_input.full_text(), command);
            assert!(!harness.app.theme_picker.open);
            assert!(harness
                .notice()
                .is_some_and(|text| text.starts_with("Slash commands cannot be queued")));
        }
        let mut harness = Harness::ready();
        start_job(&mut harness.app);

        assert!(!harness.submit("/help"));

        assert!(harness.app.chat_input.full_text().is_empty());
        assert_eq!(harness.notice(), None);
    }
}

#[tokio::test]
async fn a_command_read_during_a_turn_keeps_the_turn_loading_line() {
    let mut harness = Harness::ready();
    harness.app.session.status = Status::Generating {
        since: vibe_rs::clock::now(),
    };
    harness.app.view.loading.set_label("Thinking");

    assert!(!harness.submit("/whoami"));

    assert!(!harness.app.view.command_loading);
    assert_eq!(harness.app.view.loading.label(), "Thinking");
}

#[tokio::test]
async fn whoami_shows_a_loading_line_only_while_it_reads_the_identity() {
    let mut harness = Harness::ready();

    assert!(!harness.submit("/whoami"));
    assert!(
        harness.app.view.command_loading,
        "a cache miss reads the server"
    );
    let methods = harness.sent_methods().await;
    assert!(methods.contains(&"identity/read".to_owned()));
    assert!(methods.contains(&"providerAuth/read".to_owned()));
    assert!(matches!(
        harness.commands.try_recv(),
        Ok(CommandEvent::Whoami { .. })
    ));

    let mut cached = Harness::ready();
    let model = cached.app.session.startup_config.active_model.clone();
    cached.app.whoami.store(model, AccountReads::default());
    assert!(!cached.submit("/whoami"));
    assert!(
        !cached.app.view.command_loading,
        "a cached answer shows at once"
    );
    let methods = cached.sent_methods().await;
    assert!(!methods.contains(&"identity/read".to_owned()));
    assert!(
        methods.contains(&"providerAuth/read".to_owned()),
        "the credential source is always read fresh"
    );
}

#[tokio::test]
async fn escape_on_a_running_shell_command_interrupts_that_command_only() {
    let mut harness = Harness::ready();
    assert!(!harness.submit("!sleep 10"));
    let operation_id = harness.app.session.shell_operation_id.clone().unwrap();
    let methods = harness.sent_methods().await;
    assert_eq!(methods, ["session/shellCommand"]);
    vibe_rs::commands::shell::apply_started(&mut harness.app, &harness.client, &operation_id);

    assert!(!harness.submit("!echo done"));
    assert_eq!(harness.app.chat_input.full_text(), "!echo done");
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    vibe_rs::input::handle_key(
        &mut harness.app,
        &harness.client,
        &config_tx,
        key(KeyCode::Esc),
    );

    settle().await;
    let frames: Vec<Value> = std::iter::from_fn(|| harness.frames.try_recv().ok()).collect();
    assert_eq!(frames.len(), 1, "one interrupt, no second run");
    assert_eq!(frames[0]["method"], "session/shellCommand");
    assert_eq!(frames[0]["params"]["action"], "interrupt");
    assert_eq!(frames[0]["params"]["operationId"], operation_id.as_str());
}

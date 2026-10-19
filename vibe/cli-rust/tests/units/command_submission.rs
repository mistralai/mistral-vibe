//! Which submitted inputs exit, get refused while busy, or show a loading line, and what reaches the wire.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::{is_side_channel, submission, CommandEvent};
use vibe_rs::config;
use vibe_rs::post_ready::AccountReads;
use vibe_rs::server::Client;

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

    fn last_toast(&self) -> Option<&str> {
        self.app
            .overlays
            .toasts
            .back()
            .map(|toast| toast.text.as_str())
    }
}

/// Lets spawned requests reach the stub writer; deterministic on the current-thread test runtime.
async fn settle() {
    for _ in 0..32 {
        tokio::task::yield_now().await;
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
async fn branch_during_a_turn_is_refused_kept_and_never_forks() {
    assert!(!is_side_channel("/branch"));
    let mut harness = Harness::ready();
    harness.app.session.active_turn_id = Some("turn".into());
    harness.app.session.status = Status::Generating {
        since: std::time::Instant::now(),
    };

    assert!(!harness.submit("/branch"));

    assert_eq!(harness.app.chat_input.full_text(), "/branch");
    assert!(harness
        .last_toast()
        .is_some_and(|text| text.starts_with("Slash commands cannot be queued")));
    assert!(!harness
        .sent_methods()
        .await
        .contains(&"session/fork".to_owned()));
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
    assert!(matches!(
        harness.commands.try_recv(),
        Ok(CommandEvent::Whoami(_))
    ));

    let mut cached = Harness::ready();
    let model = cached.app.session.startup_config.active_model.clone();
    cached.app.whoami.store(model, AccountReads::default());
    assert!(!cached.submit("/whoami"));
    assert!(
        !cached.app.view.command_loading,
        "a cached answer shows at once"
    );
    assert!(!cached
        .sent_methods()
        .await
        .contains(&"identity/read".to_owned()));
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
    let escape = KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE);
    vibe_rs::input::handle_key(&mut harness.app, &harness.client, &config_tx, escape);

    settle().await;
    let frames: Vec<Value> = std::iter::from_fn(|| harness.frames.try_recv().ok()).collect();
    assert_eq!(frames.len(), 1, "one interrupt, no second run");
    assert_eq!(frames[0]["method"], "session/shellCommand");
    assert_eq!(frames[0]["params"]["action"], "interrupt");
    assert_eq!(frames[0]["params"]["operationId"], operation_id.as_str());
}

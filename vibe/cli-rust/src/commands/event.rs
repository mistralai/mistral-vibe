//! Background command events: the enum a completed slash command reports to
//! the UI reducer, plus the slot claim each dispatch makes.

use std::future::Future;
use std::sync::Arc;

use serde_json::Value;
use tokio::sync::mpsc;

use crate::app::{App, QueuedPrompt};
use crate::input::deliver;
use crate::post_ready::AccountReads;
use crate::server::Client;

/// A completed background slash command, returned to the UI reducer. Exactly one
/// per dispatch, so the reducer can tell when the UI is settled again.
pub enum CommandEvent {
    Plugins(Box<crate::plugins::Event>),
    Skills(Box<crate::skills_browser::Event>),
    /// The `/context` breakdown read, with the generation it was requested at.
    Context(Box<(u64, crate::context_app::Event)>),
    Leanstall(Box<super::leanstall::Event>),
    /// A content command's markdown body.
    Result(String),
    /// An action outcome, mounted as a `✓` row.
    Status(String),
    /// The `/status` text, rendered with its exact counts muted.
    AgentStatistics(String),
    Renamed(String),
    /// `/branch` forked the session into a detached copy.
    Branched {
        old_session_id: String,
        new_session_id: String,
        harness: Option<String>,
    },
    Error(String),
    Runtime(Value, String),
    VoiceSettings {
        runtime: Value,
        previous_enabled: bool,
        enabling_audio: bool,
    },
    /// The `/proxy-setup` settings read, or why it failed.
    ProxySettings(Result<crate::server::proto_proxy::ProxySettingsView, String>),
    /// The post-ready account and identity reads asked under `model`, for the banner.
    PostReady {
        model: String,
        reads: Option<AccountReads>,
        greeting: Option<String>,
    },
    /// An `artifacts/list` answer for `session_id`, for the bottom-bar PR link.
    ArtifactsRead {
        session_id: String,
        artifacts: Vec<crate::server::PublicSessionArtifact>,
    },
    /// The `/whoami` reads: identity and account (cached or fetched) and the
    /// `providerAuth/read` view, each `None` when its read failed.
    Whoami {
        model: String,
        reads: Option<AccountReads>,
        auth: Option<Value>,
    },
    /// A manual shell request settled; notifications already carried its output.
    ShellCompleted {
        operation_id: String,
        error: Option<String>,
    },
    /// The untrusted-config read answered; the warning body, or `None` when the
    /// folders are all acknowledged.
    UntrustedConfig(Option<String>),
    /// `session/history/clear` answered with the replacement session; the seed
    /// turn only starts once the new id is live on the main thread.
    Cleared {
        session_id: String,
        usage: Option<crate::server::TokenUsage>,
        child_sessions: Vec<crate::server::PublicChildSession>,
        /// The replaced session, when its log can be resumed.
        previous_session_id: Option<String>,
        seed: Option<QueuedPrompt>,
    },
    /// `session/compact` answered with the replacement state.
    Compacted {
        state: Box<crate::server::PublicSessionState>,
        status_id: String,
    },
    /// `session/compact` failed; settle the compact status in place.
    CompactError {
        status_id: String,
        error: String,
    },
    /// The injected `/retry` `turn/start` was accepted; busy state clears via `turn/completed`.
    RetryStarted,
    /// The `/retry` `turn/start` failed; clear the busy state and mount the error.
    RetryFailed {
        error: String,
    },
}

/// Claim a slot for one background command; the app stays busy until its event lands.
pub(super) fn dispatch(app: &mut App) -> Option<(String, mpsc::Sender<CommandEvent>)> {
    let session_id = app.session.session_id.clone()?;
    let tx = app.command_tx.clone()?;
    Some((session_id, tx))
}

/// Run `task` for the session off the main thread, delivering its answer as a command event; false without a session.
pub fn spawn_session<E, F, Fut>(app: &App, client: &Arc<Client>, task: F) -> bool
where
    E: Into<CommandEvent> + Send + 'static,
    F: FnOnce(Arc<Client>, String) -> Fut + Send + 'static,
    Fut: Future<Output = E> + Send + 'static,
{
    let Some(session_id) = app.session.session_id.clone() else {
        return false;
    };
    let tx = app.command_tx.clone();
    let client = client.clone();
    crate::work::spawn(async move {
        deliver(tx, task(client, session_id).await).await;
    });
    true
}

impl From<crate::plugins::Event> for CommandEvent {
    fn from(event: crate::plugins::Event) -> Self {
        Self::Plugins(Box::new(event))
    }
}

impl From<crate::skills_browser::Event> for CommandEvent {
    fn from(event: crate::skills_browser::Event) -> Self {
        Self::Skills(Box::new(event))
    }
}

/// Mounted once a `config/reload` lands.
pub const RELOADED_MESSAGE: &str =
    "Configuration reloaded (includes agent instructions and skills).";

pub fn reload_result(value: Value) -> CommandEvent {
    let text = RELOADED_MESSAGE.to_owned();
    match value.get("runtime") {
        // Keep the `{runtime: ...}` wrapper `apply_runtime_value` reads.
        Some(_) => CommandEvent::Runtime(value, text),
        None => CommandEvent::Status(text),
    }
}

//! Background command events: the enum a completed slash command reports to
//! the UI reducer, plus the slot claim each dispatch makes.

use serde_json::Value;
use tokio::sync::mpsc;

use crate::app::{App, QueuedPrompt};
use crate::post_ready::AccountReads;

/// A completed background slash command, returned to the UI reducer. Exactly one
/// per dispatch, so the reducer can tell when the UI is settled again.
pub enum CommandEvent {
    RemoteProject(Box<crate::vibe_code_project::Reply>),
    Result(String),
    Renamed(String),
    Error(String),
    Runtime(Value, String),
    /// The post-ready account and identity reads, for the banner.
    PostReady {
        reads: Option<AccountReads>,
        greeting: Option<String>,
    },
    /// A `/whoami` fetch that missed the cache.
    Whoami(Option<AccountReads>),
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
        seed: Option<QueuedPrompt>,
    },
    /// `session/compact` answered with the replacement state.
    Compacted {
        state: crate::server::PublicSessionState,
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
    app.commit_started();
    Some((session_id, tx))
}

/// Mounted once a `config/reload` lands, matching Python's `_reload_config`.
pub const RELOADED_MESSAGE: &str =
    "Configuration reloaded (includes agent instructions and skills).";

pub fn reload_result(value: Value) -> CommandEvent {
    let text = RELOADED_MESSAGE.to_owned();
    match value.get("runtime") {
        // Keep the `{runtime: ...}` wrapper `apply_runtime_value` reads.
        Some(_) => CommandEvent::Runtime(value, text),
        None => CommandEvent::Result(text),
    }
}

//! TUI event loop.

use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use tokio::sync::{mpsc, watch};

use crate::app::App;
use crate::commands::CommandEvent;
use crate::config;
use crate::resume_picker::Event as ResumeEvent;
use crate::server::{method, Client, Notification, SHUTDOWN_GRACE};
use crate::session_exit;
use crate::startup::StartupEvent;
use crate::voice::VoiceEvent;

mod deadlines;
mod helpers;
pub(crate) mod keys;
pub mod notifications;
mod steady;
mod suspend;

/// Re-exported for the pre-TUI update prompt, which shares the replay harness's
/// idle-marker protocol.
pub use helpers::emit_idle_marker;

const NOTIF_DRAIN_CAP: u32 = 512;
pub(crate) const REDRAW_INTERVAL: Duration = Duration::from_micros(16_667);
const IDLE_MARKER: &[u8] = b"\x1b]5379;vibe-idle\x07";
// The replay harness's batch-marker keys, shared with the wizard flow:
// the harness brackets a batched input step in them so a step's keys produce
// one marker instead of one per key. They ride the input stream, so they are
// ordered against the keys they bracket without any timing assumption.
// `pub`, not `pub(crate)`: the pre-TUI update prompt shares the protocol.
pub const HOLD_KEY: u8 = 23;
pub const RELEASE_KEY: u8 = 24;
/// Bounded command-result channel; slash-command results are sparse, drop on full.
pub const COMMAND_CHANNEL_CAP: usize = 16;
/// Bounded voice-event channel; transcription deltas burst, drop on full.
pub const VOICE_CHANNEL_CAP: usize = 256;
/// Bounded prompt-queue channel; one answer per accepted prompt.
pub const QUEUE_CHANNEL_CAP: usize = 32;
/// Bounded approval result/preview channel; one response and one preview are expected.
pub const APPROVAL_CHANNEL_CAP: usize = 4;
/// Bounded feedback-result channel; at most one eligibility or record request is active.
pub const FEEDBACK_CHANNEL_CAP: usize = 2;
/// Bounded narrator channel; at most one summary request is active.
pub const NARRATOR_CHANNEL_CAP: usize = 2;
/// How the steady loop ended: a plain quit, or the handshake's missing-key
/// verdict — the wizard is a pre-session surface (Python `run_onboarding`
/// runs from the entrypoint, never inside the TUI), so the loop hands its
/// parts back to `main` for the wizard round and its child.
pub enum LoopExit {
    /// The user exited or an error ended the run.
    Quit,
    /// The server reports a missing API key; the pre-session wizard round
    /// runs, seeded for the provider the verdict names (the server derives
    /// the env var itself at store-credential).
    NeedsOnboarding {
        provider: String,
        env_key: Option<String>,
    },
}

/// `EventLoop::run`'s result: either the run finished (exit sequence done)
/// or the loop unwound for the pre-session wizard.
pub enum RunOutcome {
    /// The run ended: the terminal is restored, the resume block printed,
    /// and the child reaped.
    Finished {
        result: Result<()>,
        summary: Option<session_exit::SessionExitSummary>,
    },
    /// The wizard must run, seeded for the provider the verdict names; the
    /// loop's parts are handed back untouched (the wizard needs the live
    /// terminal and input stream).
    NeedsOnboarding {
        parts: Box<EventLoop>,
        provider: String,
        env_key: Option<String>,
    },
}

pub struct EventLoop {
    pub terminal: crate::terminal::Tui,
    pub terminal_guard: crate::terminal::TerminalGuard,
    pub app: App,
    pub client: Arc<Client>,
    /// The live app-server child; replaced by a fresh spawn after the
    /// missing-key wizard (the old child cannot see the persisted key).
    pub child: Option<crate::server::child::ChildHandle>,
    /// The launch used for the first spawn; reused by that replacement.
    pub launch: crate::server::process::Launch,
    pub config_tx: mpsc::Sender<config::Loaded>,
    pub sources: EventSources,
    pub input: mpsc::Receiver<crossterm::event::Event>,
    pub crash_rx: watch::Receiver<bool>,
    pub shutdown: crate::server::signal::ShutdownSignal,
    /// Startup-event sender; the post-wizard handshake re-spawn reuses it.
    pub ready_tx: mpsc::Sender<StartupEvent>,
    /// Whether the first config read had no cache, forwarded to the retried handshake.
    pub show_unready_config: bool,
}

pub struct EventSources {
    pub notifications: mpsc::Receiver<Notification>,
    pub ready: mpsc::Receiver<StartupEvent>,
    pub config: mpsc::Receiver<config::Loaded>,
    pub theme: mpsc::Receiver<crate::theme_picker::Event>,
    pub model: mpsc::Receiver<crate::model_picker::Event>,
    pub log_level: mpsc::Receiver<crate::log_level_picker::Event>,
    pub thinking: mpsc::Receiver<crate::thinking_picker::Event>,
    pub agents: mpsc::Receiver<crate::agents::Event>,
    pub resume: mpsc::Receiver<ResumeEvent>,
    pub rewind: mpsc::Receiver<crate::rewind::Event>,
    pub mcp: mpsc::Receiver<crate::mcp::Event>,
    pub mcp_oauth: mpsc::Receiver<crate::mcp_oauth::Event>,
    pub connector_auth: mpsc::Receiver<crate::connector_auth::Event>,
    pub commands: mpsc::Receiver<CommandEvent>,
    pub queue: mpsc::Receiver<crate::message_queue::QueueEvent>,
    pub approval: mpsc::Receiver<crate::approval::Event>,
    pub feedback: mpsc::Receiver<crate::feedback::Event>,
    pub paste_image: mpsc::Receiver<crate::paste_image::Event>,
    pub narrator: mpsc::Receiver<crate::turn_summary::Event>,
    pub voice: mpsc::Receiver<VoiceEvent>,
    pub telemetry: mpsc::Receiver<crate::telemetry::TelemetryEvent>,
    pub files: watch::Receiver<u64>,
    pub subagents: mpsc::Receiver<crate::subagents::Event>,
    pub older_history: mpsc::Receiver<crate::older_history::Event>,
}

impl EventLoop {
    /// Fold what the pre-TUI worktree wait buffered (the gate's handshake),
    /// so the first frame is the state the event loop would have converged
    /// to: the settle rides the `Ready` event's absorbed notifications,
    /// moving the footer cwd before the terminal paints the project path.
    /// A missing-key verdict is the same exit as the live one: the rest of
    /// the stale buffer is dropped, not re-driven — the fresh child the
    /// wizard round spawns runs its own handshake.
    pub fn replay_startup(
        &mut self,
        startup: Vec<StartupEvent>,
        timings: &mut crate::startup::StartupRecorder,
    ) -> Result<Option<LoopExit>> {
        for event in startup {
            if let StartupEvent::MissingApiKey { provider, env_key } = event {
                return Ok(Some(LoopExit::NeedsOnboarding { provider, env_key }));
            }
            if crate::event_handler::apply_startup_event(
                &mut self.app,
                &self.client,
                &self.config_tx,
                event,
            ) {
                return Ok(Some(LoopExit::Quit));
            }
        }
        self.terminal.draw(|frame| self.app.draw(frame))?;
        timings.record("first_draw");
        crate::startup::mark_first_draw();
        crate::event_handler::flush_startup_telemetry(&mut self.app);
        Ok(None)
    }

    /// Run to an exit; the summary is read only on a clean exit (Python prints
    /// the resume block only when `run_textual_ui` returned a summary).
    pub async fn run(mut self) -> RunOutcome {
        let result = match self.steady().await {
            Ok(LoopExit::Quit) => Ok(()),
            Ok(LoopExit::NeedsOnboarding { provider, env_key }) => {
                return RunOutcome::NeedsOnboarding {
                    parts: Box::new(self),
                    provider,
                    env_key,
                };
            }
            Err(error) => Err(error),
        };
        // Closing input cancels the stream without joining an OS reader thread.
        self.input.close();
        drop(self.terminal_guard);
        crate::terminal::release(self.terminal);
        // Read the exit summary while the server is still alive, before the
        // session/stop drain; only a clean exit prints the resume block.
        let summary = match &result {
            Ok(()) => Some(session_exit::exit_summary(&self.app, &self.client).await),
            Err(_) => None,
        };
        // Python prints the resume block right after the TUI comes down and
        // only then runs the worktree cleanup (`_cleanup_worktree_on_exit`
        // lives in the `finally`, after `print_session_resume_message`).
        if result.is_ok() {
            session_exit::print_session_resume_message(summary.as_ref());
        }
        // Worktree exit cleanup runs while the server still serves requests:
        // session/stop is its exit signal, and the remove questions must be
        // answered before it (Python asks with the engine still alive). The
        // server discounts this session's own worktree holder, so a clean
        // created worktree removes without a prompt.
        if result.is_ok() {
            crate::worktree_exit::cleanup_on_exit(&self.app, &self.client, &mut self.shutdown)
                .await;
        }
        // Send session/stop so the app-server drains and exits cleanly; bounded
        // by one grace period or a later signal so we never hang on it.
        if let Some(sid) = self.app.session.session_id.clone() {
            let stop = stop_session(&self.client, &sid, &mut self.sources.telemetry);
            tokio::select! {
                _ = stop => {}
                _ = tokio::time::sleep(SHUTDOWN_GRACE) => {}
                _ = self.shutdown.wait() => {}
            }
        }
        // stdin EOF is the server's exit signal; without it the reader task's
        // writer clone keeps the pipe open and wait_with_grace always SIGKILLs.
        self.client.close_stdin().await;
        // Graceful child shutdown: session/stop was sent above, wait then reap.
        // TerminalGuard was dropped before the server wait, so the terminal is
        // already restored. The server's own exit code after a stop is not
        // reportable: its close() cancels the readline serve task, so it always
        // exits non-zero even on a clean stop.
        if let Some(child) = self.child.take() {
            match child.wait_with_grace().await {
                Some(status) => tracing::debug!("app-server reaped: {status}"),
                None => tracing::debug!("app-server SIGKILLed after grace"),
            }
        }
        RunOutcome::Finished { result, summary }
    }
}

/// Flush queued telemetry, then `session/stop`, so exit-time events such as
/// `/exit` reach the server before it drains.
pub async fn stop_session(
    client: &Client,
    session_id: &str,
    telemetry: &mut mpsc::Receiver<crate::telemetry::TelemetryEvent>,
) {
    crate::telemetry::flush(client, session_id, telemetry).await;
    let params = serde_json::json!({"sessionId": session_id, "reason": null});
    let _ = client.request(method::SESSION_STOP, params).await;
}

//! TUI event loop.

use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use tokio::sync::{mpsc, watch};

use crate::app::App;
use crate::commands::CommandEvent;
use crate::config;
use crate::input_thread::InputThread;
use crate::resume_picker::Event as ResumeEvent;
use crate::server::{method, Client, Notification, SHUTDOWN_GRACE};
use crate::session_exit;
use crate::startup::StartupEvent;
use crate::voice::VoiceEvent;

mod helpers;
pub mod notifications;
mod steady;
mod suspend;

const NOTIF_DRAIN_CAP: u32 = 512;
const REDRAW_INTERVAL: Duration = Duration::from_micros(16_667);
const IDLE_MARKER: &[u8] = b"\x1b]5379;vibe-idle\x07";
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
// F23/F24 are the replay harness's batch-marker keys, part of its shared input
// protocol. The harness brackets a batched input step in them so a step's keys
// produce one marker instead of one per key. They ride the input stream, so they
// are ordered against the keys they bracket without any timing assumption.
const HOLD_KEY: u8 = 23;
const RELEASE_KEY: u8 = 24;
/// Bounded event channel shared by the three runtime parties (input/server/main).
pub use crate::input_thread::EVENT_CHANNEL_CAP;

pub struct EventLoop {
    pub terminal: ratatui::DefaultTerminal,
    pub terminal_guard: crate::terminal::TerminalGuard,
    pub app: App,
    pub client: Arc<Client>,
    pub config_tx: mpsc::Sender<config::Loaded>,
    pub sources: EventSources,
    pub input: InputThread,
    pub crash_rx: watch::Receiver<bool>,
    pub shutdown: crate::server::signal::ShutdownSignal,
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
    pub files: watch::Receiver<u64>,
}

impl EventLoop {
    /// Run to quit; the summary is read only on a clean exit (Python prints the
    /// resume block only when `run_textual_ui` returned a summary).
    pub async fn run(mut self) -> (Result<()>, Option<session_exit::SessionExitSummary>) {
        let result = self.steady().await;
        // Stop input and restore the terminal before any server wait, so a hung
        // stop can never leave the shell in raw mode.
        self.input.shutdown();
        drop(self.terminal_guard);
        // Read the exit summary while the server is still alive, before the
        // session/stop drain; only a clean exit prints the resume block.
        let summary = match &result {
            Ok(()) => Some(session_exit::exit_summary(&self.app, &self.client).await),
            Err(_) => None,
        };
        // Send session/stop so the app-server drains and exits cleanly; bounded
        // by the grace period or a later signal so we never hang on it.
        if let Some(sid) = self.app.session.session_id.clone() {
            let stop = self.client.request(
                method::SESSION_STOP,
                serde_json::json!({"sessionId": sid, "reason": null}),
            );
            tokio::select! {
                _ = stop => {}
                _ = tokio::time::sleep(SHUTDOWN_GRACE) => {}
                _ = self.shutdown.wait() => {}
            }
        }
        // stdin EOF is the server's exit signal; without it the reader task's
        // writer clone keeps the pipe open and wait_with_grace always SIGKILLs.
        self.client.close_stdin().await;
        (result, summary)
    }
}

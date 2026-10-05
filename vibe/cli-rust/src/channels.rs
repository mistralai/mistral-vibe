//! The UI's channel plumbing: one bounded mpsc pair per widget that talks to
//! the main thread, wired onto `App` and folded into the event loop's sources.

use tokio::sync::{mpsc, watch};

use crate::app::App;
use crate::commands::CommandEvent;
use crate::event_loop::{
    EventSources, APPROVAL_CHANNEL_CAP, COMMAND_CHANNEL_CAP, FEEDBACK_CHANNEL_CAP,
    NARRATOR_CHANNEL_CAP, QUEUE_CHANNEL_CAP, VOICE_CHANNEL_CAP,
};
use crate::message_queue::QueueEvent;
use crate::paste_image::CHANNEL_CAP as PASTE_IMAGE_CHANNEL_CAP;
use crate::server::Notification;
use crate::startup::StartupEvent;
use crate::telemetry::{TelemetryEvent, TELEMETRY_CHANNEL_CAP};
use crate::voice::VoiceEvent;
use crate::{
    agents, approval, config, connector_auth, feedback, log_level_picker, mcp, mcp_oauth,
    model_picker, resume_picker, rewind, theme_picker, thinking_picker, turn_summary,
};

/// Every receive half the event loop selects on, plus the `config_tx` the
/// startup events replay through.
pub struct UiChannels {
    pub config_tx: mpsc::Sender<config::Loaded>,
    config: mpsc::Receiver<config::Loaded>,
    theme: mpsc::Receiver<theme_picker::Event>,
    model: mpsc::Receiver<model_picker::Event>,
    log_level: mpsc::Receiver<log_level_picker::Event>,
    thinking: mpsc::Receiver<thinking_picker::Event>,
    agents: mpsc::Receiver<agents::Event>,
    resume: mpsc::Receiver<resume_picker::Event>,
    rewind: mpsc::Receiver<rewind::Event>,
    mcp: mpsc::Receiver<mcp::Event>,
    mcp_oauth: mpsc::Receiver<mcp_oauth::Event>,
    connector_auth: mpsc::Receiver<connector_auth::Event>,
    commands: mpsc::Receiver<CommandEvent>,
    queue: mpsc::Receiver<QueueEvent>,
    approval: mpsc::Receiver<approval::Event>,
    feedback: mpsc::Receiver<feedback::Event>,
    paste_image: mpsc::Receiver<crate::paste_image::Event>,
    narrator: mpsc::Receiver<turn_summary::Event>,
    voice: mpsc::Receiver<VoiceEvent>,
    subagents: mpsc::Receiver<crate::subagents::Event>,
    older_history: mpsc::Receiver<crate::older_history::Event>,
    telemetry: mpsc::Receiver<TelemetryEvent>,
}

/// Create every UI channel and hand its send half to the widget that owns it.
pub fn connect(app: &mut App) -> UiChannels {
    let (config_tx, config) = mpsc::channel::<config::Loaded>(1);
    // Theme committed by a `config/write` response, applied on the main thread.
    let (theme_tx, theme) = mpsc::channel::<theme_picker::Event>(1);
    app.theme_picker.tx = Some(theme_tx);
    // Runtime returned by the model picker's `config/write`, applied on the main thread.
    let (model_tx, model) = mpsc::channel::<model_picker::Event>(1);
    app.model_picker.tx = Some(model_tx);
    // Log-level `config/write` answer, applied on the main thread.
    let (log_level_tx, log_level) = mpsc::channel::<log_level_picker::Event>(1);
    app.log_level_picker.tx = Some(log_level_tx);
    // Thinking-level `config/write` answer, applied on the main thread.
    let (thinking_tx, thinking) = mpsc::channel::<thinking_picker::Event>(1);
    app.thinking_picker.tx = Some(thinking_tx);
    // Runtime returned by a Shift+Tab `session/agent/update`, applied on the main thread.
    let (agents_tx, agents) = mpsc::channel::<agents::Event>(1);
    app.agents.tx = Some(agents_tx);
    let (resume_tx, resume) = mpsc::channel::<resume_picker::Event>(8);
    app.resume_picker.tx = Some(resume_tx);
    // Rewind reads and rewinds, applied on the main thread.
    let (rewind_tx, rewind) = mpsc::channel::<rewind::Event>(8);
    app.rewind.tx = Some(rewind_tx);
    // MCP reads, refreshes and toggles, applied on the main thread.
    let (mcp_tx, mcp) = mpsc::channel::<mcp::Event>(8);
    app.mcp.tx = Some(mcp_tx);
    // `mcp/login` answers of the OAuth bottom-app, applied on the main thread.
    let (mcp_oauth_tx, mcp_oauth) = mpsc::channel::<mcp_oauth::Event>(8);
    app.mcp_oauth.tx = Some(mcp_oauth_tx);
    // Connector auth-read and refresh answers, applied on the main thread.
    let (connector_auth_tx, connector_auth) = mpsc::channel::<connector_auth::Event>(8);
    app.connector_auth.tx = Some(connector_auth_tx);
    let (command_tx, commands) = mpsc::channel::<CommandEvent>(COMMAND_CHANNEL_CAP);
    app.command_tx = Some(command_tx);
    // Prompt-queue answers (enqueue accepted or rejected), applied on the main thread.
    let (queue_tx, queue) = mpsc::channel::<QueueEvent>(QUEUE_CHANNEL_CAP);
    app.queue.tx = Some(queue_tx);
    let (approval_tx, approval) = mpsc::channel::<approval::Event>(APPROVAL_CHANNEL_CAP);
    app.approval.tx = Some(approval_tx);
    let (feedback_tx, feedback) = mpsc::channel::<feedback::Event>(FEEDBACK_CHANNEL_CAP);
    app.feedback.tx = Some(feedback_tx);
    // Narrator summarize answers, applied on the main thread.
    let (narrator_tx, narrator) = mpsc::channel::<turn_summary::Event>(NARRATOR_CHANNEL_CAP);
    app.narrator.tx = Some(narrator_tx);
    let (paste_image_tx, paste_image) =
        mpsc::channel::<crate::paste_image::Event>(PASTE_IMAGE_CHANNEL_CAP);
    app.paste_image.tx = Some(paste_image_tx);
    // Recording pipeline -> UI updates (transcript text, notices, state).
    let (voice_tx, voice) = mpsc::channel::<VoiceEvent>(VOICE_CHANNEL_CAP);
    app.voice.tx = Some(voice_tx);
    // Child-transcript fetch answers, applied on the main thread.
    let (subagents_tx, subagents) = mpsc::channel::<crate::subagents::Event>(8);
    app.subagents.tx = Some(subagents_tx);
    // Older history pages, one in flight, applied on the main thread.
    let (older_history_tx, older_history) = mpsc::channel::<crate::older_history::Event>(1);
    app.older_history.tx = Some(older_history_tx);
    // Analytics queued by the reducer, sent by the event loop.
    let (telemetry_tx, telemetry) = mpsc::channel::<TelemetryEvent>(TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(telemetry_tx);
    UiChannels {
        config_tx,
        config,
        theme,
        model,
        log_level,
        thinking,
        agents,
        resume,
        rewind,
        mcp,
        mcp_oauth,
        connector_auth,
        commands,
        queue,
        approval,
        feedback,
        paste_image,
        narrator,
        voice,
        subagents,
        older_history,
        telemetry,
    }
}

impl UiChannels {
    /// The event loop's sources, once the spawn produced the notification
    /// stream, the ready channel, and the file index's change feed.
    pub fn sources(
        self,
        notifications: mpsc::Receiver<Notification>,
        ready: mpsc::Receiver<StartupEvent>,
        files: watch::Receiver<u64>,
    ) -> EventSources {
        EventSources {
            notifications,
            ready,
            config: self.config,
            theme: self.theme,
            model: self.model,
            log_level: self.log_level,
            thinking: self.thinking,
            agents: self.agents,
            resume: self.resume,
            rewind: self.rewind,
            mcp: self.mcp,
            mcp_oauth: self.mcp_oauth,
            connector_auth: self.connector_auth,
            commands: self.commands,
            queue: self.queue,
            approval: self.approval,
            feedback: self.feedback,
            paste_image: self.paste_image,
            narrator: self.narrator,
            voice: self.voice,
            subagents: self.subagents,
            older_history: self.older_history,
            telemetry: self.telemetry,
            files,
        }
    }
}

//! The UI's channel plumbing: the shared widget-answer channel plus the few
//! streams with their own capacity or drain rules, folded into the event loop's sources.

use tokio::sync::{mpsc, watch};

use crate::app::App;
use crate::commands::CommandEvent;
use crate::config;
use crate::event_loop::{EventSources, COMMAND_CHANNEL_CAP, VOICE_CHANNEL_CAP};
use crate::server::Notification;
use crate::startup::StartupEvent;
use crate::telemetry::{TelemetryEvent, TELEMETRY_CHANNEL_CAP};
use crate::ui_event::{self, UiEvent};
use crate::voice::VoiceEvent;

/// Every receive half the event loop selects on, plus the `config_tx` the
/// startup events replay through.
pub struct UiChannels {
    pub config_tx: mpsc::Sender<config::Loaded>,
    config: mpsc::Receiver<config::Loaded>,
    events: mpsc::Receiver<UiEvent>,
    commands: mpsc::Receiver<CommandEvent>,
    voice: mpsc::Receiver<VoiceEvent>,
    telemetry: mpsc::Receiver<TelemetryEvent>,
}

/// Create every UI channel and hand its send half to the app.
pub fn connect(app: &mut App) -> UiChannels {
    let (config_tx, config) = mpsc::channel::<config::Loaded>(1);
    let (events_tx, events) = mpsc::channel::<UiEvent>(ui_event::CHANNEL_CAP);
    app.events = Some(events_tx);
    let (command_tx, commands) = mpsc::channel::<CommandEvent>(COMMAND_CHANNEL_CAP);
    app.command_tx = Some(command_tx);
    // Recording pipeline -> UI updates (transcript text, notices, state).
    let (voice_tx, voice) = mpsc::channel::<VoiceEvent>(VOICE_CHANNEL_CAP);
    app.voice.tx = Some(voice_tx);
    // Analytics queued by the reducer, sent by the event loop.
    let (telemetry_tx, telemetry) = mpsc::channel::<TelemetryEvent>(TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(telemetry_tx);
    UiChannels {
        config_tx,
        config,
        events,
        commands,
        voice,
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
            events: self.events,
            commands: self.commands,
            voice: self.voice,
            telemetry: self.telemetry,
            files,
        }
    }
}

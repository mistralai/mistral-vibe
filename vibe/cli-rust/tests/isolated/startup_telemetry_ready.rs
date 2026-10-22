//! `vibe.startup` as a real startup sends it; separate binary for the process-global clock and VIBE_HOME.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::cli::StartupResume;
use vibe_rs::config;
use vibe_rs::event_handler::{apply_startup_event, flush_startup_telemetry};
use vibe_rs::server::Client;
use vibe_rs::startup::{
    first_frame_duration_ms, mark_first_draw, Attach, ConfigRead, Ready, StartupEvent,
    StartupRecorder,
};
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::utils::startup_cache::StartupConfig;

/// Guards VIBE_HOME, which is process-global across this binary's tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

struct Startup {
    app: App,
    client: Arc<Client>,
    telemetry: mpsc::Receiver<TelemetryEvent>,
    _home: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

impl Startup {
    /// A starting app whose first frame is already painted.
    fn painted() -> Self {
        let guard = ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let home = tempfile::tempdir().unwrap();
        std::env::set_var("VIBE_HOME", home.path());
        let _clock = StartupRecorder::new();
        mark_first_draw();
        let mut app = App::default();
        let (tx, telemetry) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
        app.telemetry_tx = Some(tx);
        Self {
            app,
            client: Arc::new(Client::stub()),
            telemetry,
            _home: home,
            _guard: guard,
        }
    }

    fn apply(&mut self, event: StartupEvent) {
        let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
        apply_startup_event(&mut self.app, &self.client, &config_tx, event);
    }

    fn ready(&mut self, is_cold_start: bool) {
        self.ready_with(StartupConfig::default(), is_cold_start);
    }

    fn ready_with(&mut self, startup_cache: StartupConfig, is_cold_start: bool) {
        let ready = Ready {
            startup_cache,
            config: ConfigRead {
                enable_update_checks: false,
                show_greeting: false,
                ..ConfigRead::default()
            },
            state: json!({"eventId": 0, "session": {"id": "session"}}),
            attach: Attach::Started,
            runtime: json!({"runtime": {}}),
            is_cold_start,
            settled: None,
            prepared: None,
            absorbed: Vec::new(),
        };
        self.apply(StartupEvent::Ready(Box::new(ready)));
    }

    /// The `vibe.startup` events sent so far.
    fn startup_events(&mut self) -> Vec<BTreeMap<String, Value>> {
        std::iter::from_fn(|| self.telemetry.try_recv().ok())
            .filter(|sent| sent.name == event::STARTUP)
            .map(|sent| sent.properties)
            .collect()
    }
}

#[test]
fn the_first_frame_is_stamped_once() {
    let _startup = Startup::painted();
    let first = first_frame_duration_ms();
    assert!(first.is_some());

    std::thread::sleep(Duration::from_millis(20));
    mark_first_draw();

    assert_eq!(first_frame_duration_ms(), first);
}

#[tokio::test]
async fn ready_sends_one_startup_event_with_agent_ready_after_the_first_frame() {
    let mut startup = Startup::painted();
    startup.ready(true);
    flush_startup_telemetry(&mut startup.app);

    let events = startup.startup_events();
    assert_eq!(events.len(), 1, "sent exactly once");
    let properties = &events[0];
    let first_frame = properties["first_frame_duration_ms"].as_u64().unwrap();
    let agent_ready = properties["agent_ready_duration_ms"].as_u64().unwrap();
    assert!(agent_ready >= first_frame);
    assert_eq!(properties["session_init_duration_ms"], Value::Null);
    assert_eq!(properties["is_cold_start"], true);
    assert_eq!(properties["mouse_capture"], true);
}

#[tokio::test]
async fn a_config_without_mouse_capture_reports_it_off() {
    let mut startup = Startup::painted();
    let config = StartupConfig {
        enable_mouse_capture: false,
        ..StartupConfig::default()
    };
    startup.ready_with(config, true);

    assert_eq!(startup.startup_events()[0]["mouse_capture"], false);
}

#[tokio::test]
async fn a_warm_start_without_picker_or_trust_prompt_reports_all_three_false() {
    let mut startup = Startup::painted();
    startup.app.session.startup_resume = StartupResume::Picker;
    startup.ready(false);

    let properties = &startup.startup_events()[0];
    assert_eq!(properties["is_cold_start"], false);
    assert_eq!(
        properties["show_resume_picker"], false,
        "requested but its list never arrived"
    );
    assert_eq!(properties["prompt_for_workspace_trust"], false);
}

#[tokio::test]
async fn the_picker_and_trust_prompt_shown_before_ready_are_reported() {
    let mut startup = Startup::painted();
    startup.app.session.startup_resume = StartupResume::Picker;
    let details = serde_json::from_value(json!({"cwd": "/workspace"})).unwrap();
    startup.apply(StartupEvent::Trust(Box::new(details)));
    let listed = json!({"items": [{"id": "saved", "title": "Saved", "updatedAt": 1}]});
    startup.apply(StartupEvent::Sessions(Box::new(listed)));
    assert!(startup.app.resume_picker.open);

    startup.ready(true);

    let properties = &startup.startup_events()[0];
    assert_eq!(properties["show_resume_picker"], true);
    assert_eq!(properties["prompt_for_workspace_trust"], true);
}

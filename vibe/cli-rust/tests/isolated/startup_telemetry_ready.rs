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
use vibe_rs::update_notifier::cache::{
    FileSystemUpdateCacheRepository, UpdateCache, UpdateCacheRepository,
};
use vibe_rs::utils::cache_store;
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
        self.ready_runtime(startup_cache, is_cold_start, json!({"runtime": {}}));
    }

    fn ready_runtime(&mut self, startup_cache: StartupConfig, is_cold_start: bool, runtime: Value) {
        let ready = Ready {
            startup_cache,
            config: ConfigRead {
                enable_update_checks: false,
                show_greeting: false,
                ..ConfigRead::default()
            },
            state: json!({"eventId": 0, "session": {"id": "session"}}),
            attach: Attach::Started,
            runtime,
            is_cold_start,
            settled: None,
            prepared: None,
            absorbed: Vec::new(),
        };
        self.apply(StartupEvent::Ready(Box::new(ready)));
    }

    /// The `vibe.startup` events sent so far.
    fn startup_events(&mut self) -> Vec<BTreeMap<String, Value>> {
        self.events(event::STARTUP)
    }

    /// The events named `name` sent so far; the others are dropped.
    fn events(&mut self, name: &str) -> Vec<BTreeMap<String, Value>> {
        std::iter::from_fn(|| self.telemetry.try_recv().ok())
            .filter(|sent| sent.name == name)
            .map(|sent| sent.properties)
            .collect()
    }

    /// The next event named `name`, waiting for async effects to send it.
    async fn next_event(&mut self, name: &str) -> BTreeMap<String, Value> {
        let wait = async {
            loop {
                let sent = self.telemetry.recv().await.expect("telemetry channel open");
                if sent.name == name {
                    return sent.properties;
                }
            }
        };
        tokio::time::timeout(Duration::from_secs(30), wait)
            .await
            .expect("event sent")
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
    assert_eq!(properties["vim_keybindings"], false);
}

#[tokio::test]
async fn a_config_with_vim_keybindings_reports_it_on() {
    let mut startup = Startup::painted();
    let runtime = json!({"runtime": {"config": {"vimKeybindings": true}}});
    startup.ready_runtime(StartupConfig::default(), true, runtime);

    assert_eq!(startup.startup_events()[0]["vim_keybindings"], true);
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

#[tokio::test]
async fn the_install_source_of_the_last_update_check_is_reported_while_its_tag_holds() {
    let mut startup = Startup::painted();
    write_update_cache("brew", 100, 100);
    startup.ready(true);
    assert_eq!(startup.startup_events()[0]["install_source"], "brew");
}

#[tokio::test]
async fn a_voided_install_source_tag_is_not_reported() {
    let mut startup = Startup::painted();
    write_update_cache("brew", 200, 100);
    startup.ready(true);
    assert_eq!(startup.startup_events()[0]["install_source"], Value::Null);
}

#[tokio::test]
async fn a_new_version_reports_the_change_once_with_this_installs_manager() {
    let mut startup = Startup::painted();
    let current = env!("CARGO_PKG_VERSION");
    write_update_cache("brew", 100, 100);
    cache_store::write_string("telemetry", "last_run_version", "0.0.1");

    startup.ready(true);

    let change = startup.next_event(event::VERSION_UPDATED).await;
    assert_eq!(change["from_version"], "0.0.1");
    assert_eq!(change["to_version"], current);
    // A test binary is neither a uv tool nor a brew formula.
    assert_eq!(change["install_source"], "pypi");
    assert_eq!(
        cache_store::read_string("telemetry", "last_run_version").as_deref(),
        Some(current)
    );
}

#[tokio::test]
async fn an_unchanged_version_reports_nothing() {
    let mut startup = Startup::painted();
    cache_store::write_string("telemetry", "last_run_version", env!("CARGO_PKG_VERSION"));

    startup.ready(true);

    let change = startup.next_event(event::VERSION_UPDATED);
    assert!(tokio::time::timeout(Duration::from_secs(2), change)
        .await
        .is_err());
}

#[tokio::test]
async fn a_first_run_only_stamps_its_version() {
    let mut startup = Startup::painted();

    startup.ready(true);

    assert!(startup.events(event::VERSION_UPDATED).is_empty());
    assert_eq!(
        cache_store::read_string("telemetry", "last_run_version").as_deref(),
        Some(env!("CARGO_PKG_VERSION"))
    );
}

fn write_update_cache(source: &str, stored_at: i64, source_stored_at: i64) {
    let cache = UpdateCache {
        latest_version: "0.0.1".to_owned(),
        stored_at_timestamp: stored_at,
        seen_whats_new_version: None,
        dismissed_version: None,
        source: Some(source.to_owned()),
        source_stored_at: Some(source_stored_at),
    };
    FileSystemUpdateCacheRepository.set(&cache).unwrap();
}

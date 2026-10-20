//! `vibe.startup` waits for the first frame; separate binary for the process-global startup clock.

use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::event_handler::flush_startup_telemetry;
use vibe_rs::startup::{mark_first_draw, StartupRecorder};
use vibe_rs::telemetry::{self, event, TelemetryEvent};

#[test]
fn startup_event_is_held_until_the_first_frame_then_sent_once() {
    let _clock = StartupRecorder::new();
    let mut app = App::default();
    let (tx, mut rx) = mpsc::channel::<TelemetryEvent>(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);
    app.session.startup_telemetry = Some(telemetry::bmap(json!({"is_cold_start": false})));

    flush_startup_telemetry(&mut app);
    assert!(
        rx.try_recv().is_err(),
        "a worktree run replays Ready before its first draw"
    );

    mark_first_draw();
    flush_startup_telemetry(&mut app);
    let startup = rx.try_recv().expect("sent once the frame is painted");
    assert_eq!(startup.name, event::STARTUP);
    assert!(startup.properties["first_frame_duration_ms"].is_u64());

    flush_startup_telemetry(&mut app);
    assert!(rx.try_recv().is_err(), "sent only once");
}

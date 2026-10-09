use serde_json::json;
use tokio::sync::mpsc;

use super::*;
use crate::telemetry::{TelemetryEvent, TELEMETRY_CHANNEL_CAP};
use crate::turn_summary;

struct Fixture {
    app: App,
    telemetry: mpsc::Receiver<TelemetryEvent>,
    stop: Arc<AtomicBool>,
    task: tokio::task::JoinHandle<()>,
}

/// A clip whose TTS request is in flight: the row still reads `summarizing`.
fn synthesizing() -> Fixture {
    let mut app = App::default();
    let (tx, telemetry) = mpsc::channel(TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);
    let stop = Arc::new(AtomicBool::new(false));
    let task = tokio::spawn(std::future::pending::<()>());
    app.narrator.speech_id = 1;
    app.narrator.speech = Some(Speech {
        id: 1,
        stop: stop.clone(),
        task: task.abort_handle(),
    });
    app.narrator.state = NarratorState::Summarizing;
    app.narrator.tracking.reset();
    Fixture {
        app,
        telemetry,
        stop,
        task,
    }
}

fn drained(rx: &mut mpsc::Receiver<TelemetryEvent>) -> Vec<TelemetryEvent> {
    std::iter::from_fn(|| rx.try_recv().ok()).collect()
}

#[tokio::test]
async fn playback_start_and_end_report_play_started_then_completed() {
    let mut f = synthesizing();
    apply_event(&mut f.app, Event::Speaking { id: 1 });
    assert_eq!(f.app.narrator.state, NarratorState::Speaking);
    let started = drained(&mut f.telemetry);
    assert_eq!(started.len(), 1);
    assert_eq!(started[0].name, event::READ_ALOUD_PLAY_STARTED);
    assert_eq!(
        started[0].properties["read_aloud_session_id"],
        json!(f.app.narrator.tracking.session_id)
    );
    apply_event(&mut f.app, Event::Finished { id: 1 });
    assert_eq!(f.app.narrator.state, NarratorState::Idle);
    assert!(f.app.narrator.speech.is_none());
    let ended = drained(&mut f.telemetry);
    assert_eq!(ended[0].name, event::READ_ALOUD_ENDED);
    assert_eq!(ended[0].properties["status"], "completed");
    assert_eq!(ended[0].properties["error_type"], json!(null));
}

#[tokio::test]
async fn a_failed_clip_settles_idle_with_its_error_type() {
    let mut f = synthesizing();
    apply_event(
        &mut f.app,
        Event::Failed {
            id: 1,
            error_type: "SDKError",
        },
    );
    assert_eq!(f.app.narrator.state, NarratorState::Idle);
    let ended = drained(&mut f.telemetry);
    assert_eq!(ended.len(), 1);
    assert_eq!(ended[0].properties["status"], "error");
    assert_eq!(ended[0].properties["error_type"], "SDKError");
}

#[tokio::test]
async fn cancel_stops_the_clip_and_drops_its_late_events() {
    let mut f = synthesizing();
    apply_event(&mut f.app, Event::Speaking { id: 1 });
    drained(&mut f.telemetry);
    assert!(turn_summary::cancel(&mut f.app));
    assert!(f.stop.load(Ordering::Relaxed));
    assert!(f.task.await.unwrap_err().is_cancelled());
    let ended = drained(&mut f.telemetry);
    assert_eq!(ended.len(), 1);
    assert_eq!(ended[0].properties["status"], "canceled");
    apply_event(&mut f.app, Event::Speaking { id: 1 });
    apply_event(&mut f.app, Event::Finished { id: 1 });
    assert_eq!(f.app.narrator.state, NarratorState::Idle);
    assert!(drained(&mut f.telemetry).is_empty());
}

#[test]
fn speech_config_follows_the_runtime_snapshot() {
    let mut app = App::default();
    let runtime = json!({"runtime": {"config": {"speech": {
        "model": {"name": "tts", "voice": "v", "responseFormat": "wav"},
        "provider": {"apiBase": "https://api.mistral.ai", "apiKeyEnvVar": "K"},
    }}}});
    apply_runtime(&mut app, &runtime);
    assert_eq!(app.narrator.speech_config.as_ref().unwrap().name, "tts");
    apply_runtime(&mut app, &json!({"runtime": {"config": {}}}));
    assert!(app.narrator.speech_config.is_none());
}

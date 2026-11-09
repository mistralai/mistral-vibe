//! Tracked work keeps the client busy until its result has been handed over, cleanup included.

use std::sync::Arc;
use std::time::Duration;

use tokio::sync::oneshot;
use vibe_rs::server::Client;
use vibe_rs::telemetry::{self, TelemetryEvent};
use vibe_rs::work::{self, Work};

async fn finished() {
    tokio::time::timeout(Duration::from_secs(5), work::finished())
        .await
        .expect("the last tracked task wakes its waiter");
}

#[tokio::test]
async fn a_tracked_task_is_busy_until_it_ends_and_then_wakes_the_loop() {
    let (release, released) = oneshot::channel::<()>();
    work::spawn(async move {
        let _ = released.await;
    });
    assert!(!work::idle(), "busy before the task even runs");
    release.send(()).unwrap();
    finished().await;
    assert!(work::idle());
}

#[tokio::test]
async fn a_daemon_never_holds_the_client_busy() {
    let task = work::spawn_daemon(std::future::pending::<()>());
    assert!(work::idle());
    task.abort();
}

struct SendsOnDrop(Option<oneshot::Sender<bool>>);

impl Drop for SendsOnDrop {
    fn drop(&mut self) {
        let _ = self.0.take().unwrap().send(work::idle());
    }
}

#[tokio::test]
async fn an_aborted_task_runs_its_cleanup_before_it_stops_counting() {
    let (seen, idle_in_drop) = oneshot::channel();
    let guard = SendsOnDrop(Some(seen));
    let task = work::spawn(async move {
        let _guard = guard;
        std::future::pending::<()>().await;
    });
    tokio::task::yield_now().await;
    task.abort();
    assert!(!idle_in_drop.await.unwrap(), "cleanup still counts as work");
    finished().await;
    assert!(work::idle());
}

#[tokio::test]
async fn a_handed_over_guard_keeps_the_client_busy() {
    let (stop, stopping) = oneshot::channel::<Work>();
    stop.send(Work::start()).ok().unwrap();
    assert!(
        !work::idle(),
        "the guard counts while it waits in the channel"
    );
    drop(stopping);
    assert!(work::idle());
}

#[tokio::test]
async fn sent_telemetry_keeps_the_client_busy_until_it_is_answered() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let event = TelemetryEvent {
        name: "vibe.test",
        properties: Default::default(),
    };
    telemetry::send(&client, "session", event);
    assert!(!work::idle(), "busy before its task runs");
    frames.recv().await.expect("telemetry frame");
    finished().await;
}

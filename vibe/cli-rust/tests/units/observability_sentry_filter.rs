//! Only fatal ERROR records reach Sentry; plain error logs stay in the log file.

use std::sync::{Arc, Mutex};

use tracing::{Event, Subscriber};
use tracing_subscriber::layer::{Context, Layer, SubscriberExt as _};
use vibe_rs::observability::logging::reports_to_sentry;

struct Decisions(Arc<Mutex<Vec<bool>>>);

impl<S: Subscriber> Layer<S> for Decisions {
    fn on_event(&self, event: &Event<'_>, _ctx: Context<'_, S>) {
        self.0
            .lock()
            .unwrap()
            .push(reports_to_sentry(event.metadata()));
    }
}

#[test]
fn only_fatal_errors_become_sentry_events() {
    let decisions = Arc::new(Mutex::new(Vec::new()));
    let subscriber = tracing_subscriber::registry().with(Decisions(decisions.clone()));
    tracing::subscriber::with_default(subscriber, || {
        tracing::error!(vibe_boundary = "panic", fatal = true, "vibe-rs panicked");
        tracing::error!(len = 1, "frame exceeds MAX_FRAME; dropped");
        tracing::warn!(fatal = true, "not an error");
    });
    assert_eq!(*decisions.lock().unwrap(), vec![true, false, false]);
}

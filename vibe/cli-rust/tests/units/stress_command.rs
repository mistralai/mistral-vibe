//! `/stress` animates while its task runs without touching the session status.

use vibe_rs::app::Status;
use vibe_rs::commands::stress;

use crate::support::{stub, with_status};

#[tokio::test]
async fn finished_stress_run_leaves_status_ready() {
    let mut app = with_status(Status::Ready);

    stress::toggle(&mut app, &stub(), "1");
    app.stress
        .as_mut()
        .expect("stress task started")
        .await
        .expect("stress task completes");

    assert!(matches!(app.session.status, Status::Ready));
    assert!(!stress::running(&app));
}

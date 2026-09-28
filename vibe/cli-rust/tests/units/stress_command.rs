//! `/stress` animates while its task runs without touching the session status.

use std::sync::Arc;

use vibe_rs::app::{App, Status};
use vibe_rs::commands::stress;
use vibe_rs::server::Client;

#[tokio::test]
async fn finished_stress_run_leaves_status_ready() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    let client = Arc::new(Client::stub());

    stress::toggle(&mut app, &client, "1");
    app.stress
        .as_mut()
        .expect("stress task started")
        .await
        .expect("stress task completes");

    assert!(matches!(app.session.status, Status::Ready));
    assert!(!stress::running(&app));
}

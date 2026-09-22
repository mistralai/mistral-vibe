//! Shutdown-signal handler installation.

use vibe_rs::server::signal;

#[tokio::test]
async fn shutdown_signal_installs() {
    assert!(signal::install().is_ok());
}

#[tokio::test]
async fn shutdown_signal_installs_more_than_once() {
    assert!(signal::install().is_ok());
    assert!(signal::install().is_ok());
}

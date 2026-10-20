//! The headless interrupt arms SIGTERM and SIGHUP, not just SIGINT
//! (Bugbot: SIGTERM used to hit the default disposition and orphan the
//! app-server's process group, since `kill_on_drop` teardown is Drop-only).

#![cfg(unix)]

use std::time::Duration;

use vibe_rs::server::signal;

#[tokio::test]
async fn shutdown_wait_completes_on_sigterm_and_sighup() {
    let mut term = signal::install().expect("install signal handlers");
    // Safe: the tokio handler installed above catches the signal, so the
    // default disposition never runs and the test process survives.
    unsafe { libc::raise(libc::SIGTERM) };
    tokio::time::timeout(Duration::from_secs(2), term.wait())
        .await
        .expect("SIGTERM must complete shutdown.wait()");

    let mut hup = signal::install().expect("reinstall signal handlers");
    unsafe { libc::raise(libc::SIGHUP) };
    tokio::time::timeout(Duration::from_secs(2), hup.wait())
        .await
        .expect("SIGHUP must complete shutdown.wait()");
}

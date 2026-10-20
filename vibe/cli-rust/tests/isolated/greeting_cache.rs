//! The once-per-24h greeting gate over `[greeting]` in an isolated `cache.toml`.

// The env guard must span the post-ready awaits that touch VIBE_HOME.
#![allow(clippy::await_holding_lock)]

use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::commands::CommandEvent;
use vibe_rs::server::Client;
use vibe_rs::utils::greeting_cache;

/// Guards VIBE_HOME, which is process-global across this binary's tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

const HOUR: i64 = 60 * 60;

/// Holds the env lock and points VIBE_HOME at a fresh temp dir.
fn isolated_home() -> (MutexGuard<'static, ()>, tempfile::TempDir) {
    let guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = tempfile::tempdir().unwrap();
    std::env::set_var("VIBE_HOME", home.path());
    (guard, home)
}

fn shown_ago(home: &tempfile::TempDir, seconds: i64) {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let text = format!(
        "[update_cache]\nlatest_version = \"1.0.0\"\n\n[greeting]\nlast_shown_at = {}\n",
        now - seconds
    );
    std::fs::write(home.path().join("cache.toml"), text).unwrap();
}

fn cache_toml(home: &tempfile::TempDir) -> String {
    std::fs::read_to_string(home.path().join("cache.toml")).unwrap_or_default()
}

#[test]
fn a_greeting_shown_less_than_a_day_ago_is_suppressed() {
    let (_guard, home) = isolated_home();
    shown_ago(&home, HOUR);
    assert!(!greeting_cache::should_show());
}

#[test]
fn a_greeting_shown_more_than_a_day_ago_shows_again() {
    let (_guard, home) = isolated_home();
    shown_ago(&home, 25 * HOUR);
    assert!(greeting_cache::should_show());
}

#[test]
fn stamping_the_greeting_keeps_the_other_cache_sections() {
    let (_guard, home) = isolated_home();
    assert!(greeting_cache::should_show(), "no cache shows the greeting");
    shown_ago(&home, 25 * HOUR);

    greeting_cache::mark_shown();

    assert!(!greeting_cache::should_show());
    assert!(cache_toml(&home).contains("latest_version = \"1.0.0\""));
}

#[tokio::test]
async fn a_missing_identity_greets_nobody_and_stamps_nothing() {
    let (_guard, home) = isolated_home();
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    let (tx, mut commands) = mpsc::channel(8);
    app.command_tx = Some(tx);
    // The stub answers every read with `null`: no identity, no first name.
    let (client, _frames) = Client::stub_answering();

    vibe_rs::post_ready::fetch(&mut app, &Arc::new(client), true);
    let event = tokio::time::timeout(Duration::from_secs(5), commands.recv())
        .await
        .expect("the post-ready reads answer")
        .expect("an event");

    let CommandEvent::PostReady { reads, greeting } = event else {
        panic!("expected the post-ready answer");
    };
    assert!(reads.is_some_and(|reads| reads.identity.is_null()));
    assert_eq!(greeting, None);
    assert!(!cache_toml(&home).contains("[greeting]"));
    assert!(greeting_cache::should_show());
}

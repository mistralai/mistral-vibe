//! Voice and narrator settings follow the accepted runtime; cache writes use an isolated Vibe home.

use std::sync::{Mutex, MutexGuard};

use crossterm::event::{KeyCode, KeyEvent};
use serde_json::{json, Value};
use vibe_rs::app::{App, ToastSeverity};

/// Guards VIBE_HOME, which is process-global across this binary's tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

/// Holds the env lock and points VIBE_HOME at a fresh temp dir.
fn isolated_home() -> (MutexGuard<'static, ()>, tempfile::TempDir) {
    let guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = tempfile::tempdir().unwrap();
    std::env::set_var("VIBE_HOME", home.path());
    (guard, home)
}

fn runtime(voice: bool, narrator: bool) -> Value {
    json!({"runtime": {"config": {
        "theme": "ansi-dark",
        "activeModel": {"displayName": "devstral", "thinking": "off"},
        "voiceModeEnabled": voice,
        "narratorEnabled": narrator,
    }}})
}

#[test]
fn reopened_settings_show_the_saved_server_values() {
    let (_guard, home) = isolated_home();
    let mut app = App::default();

    vibe_rs::voice_app::apply_saved(&mut app, &runtime(true, true), false, false);
    vibe_rs::voice_app::open(&mut app);

    assert_eq!(app.voice_app.values, [true, true]);
    assert!(home.path().join("ui_startup_config.json").exists());
}

#[test]
fn unavailable_audio_warns_without_undoing_the_accepted_setting() {
    let (_guard, _home) = isolated_home();
    let mut app = App::default();

    vibe_rs::voice_app::apply_saved(&mut app, &runtime(true, false), false, true);

    assert!(app.voice.mode_enabled, "the server accepted the change");
    let warning = app
        .overlays
        .toasts
        .iter()
        .find(|toast| matches!(toast.severity, ToastSeverity::Warning));
    if cfg!(feature = "voice") {
        assert!(warning.is_none());
    } else {
        assert!(warning.is_some_and(|toast| toast
            .text
            .starts_with("Audio setting saved, but audio is unavailable")));
    }
}

#[tokio::test]
async fn the_narrator_turns_on_only_once_the_server_accepts_it() {
    let (_guard, _home) = isolated_home();
    let mut app = App::default();
    let client = std::sync::Arc::new(vibe_rs::server::Client::stub());

    vibe_rs::voice_app::open(&mut app);
    for code in [KeyCode::Down, KeyCode::Enter] {
        vibe_rs::voice_app::handle_key(&mut app, &client, KeyEvent::from(code));
    }
    assert_eq!(
        app.voice_app.values,
        [false, true],
        "the draft holds the pick"
    );
    assert!(!app.session.startup_config.narrator_enabled);

    vibe_rs::voice_app::apply_saved(&mut app, &runtime(false, true), false, true);
    assert!(app.session.startup_config.narrator_enabled);
}

#[test]
fn a_runtime_refresh_turns_the_narrator_on_without_a_restart() {
    let (_guard, _home) = isolated_home();
    let mut app = App::default();
    assert!(!app.session.startup_config.narrator_enabled);

    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime(false, true));
    assert!(app.session.startup_config.narrator_enabled);

    vibe_rs::event_handler::apply_runtime_value(&mut app, &runtime(false, false));
    assert!(!app.session.startup_config.narrator_enabled);
}

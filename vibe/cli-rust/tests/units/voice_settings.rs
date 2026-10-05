//! Voice configuration projection and recording lifecycle contracts.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::voice::TranscribeState;
use vibe_rs::voice_app::apply_runtime;

#[test]
fn disabling_voice_cancels_recording_and_flushing() {
    for state in [TranscribeState::Recording, TranscribeState::Flushing] {
        let mut app = App::default();
        app.voice.mode_enabled = true;
        app.voice.transcribe_state = state;

        apply_runtime(
            &mut app,
            &json!({"runtime": {"config": {"voiceModeEnabled": false}}}),
        );

        assert!(!app.voice.mode_enabled);
        assert!(!app.recording_active());
    }
}

#[test]
fn enabling_voice_does_not_start_recording() {
    let mut app = App::default();

    apply_runtime(
        &mut app,
        &json!({"runtime": {"config": {"voiceModeEnabled": true}}}),
    );

    assert!(app.voice.mode_enabled);
    assert!(!app.recording_active());
}

#[test]
fn older_runtime_without_voice_preserves_the_last_known_setting() {
    let mut app = App::default();
    app.voice.mode_enabled = true;
    app.voice.transcribe_state = TranscribeState::Recording;

    apply_runtime(&mut app, &json!({"runtime": {"config": {}}}));

    assert!(app.voice.mode_enabled);
    assert!(app.recording_active());
}

#[test]
fn escape_without_changes_writes_nothing() {
    let mut app = vibe_rs::app::App::default();
    let (tx, mut rx) = tokio::sync::mpsc::channel(1);
    app.command_tx = Some(tx);
    app.session.session_id = Some("session-1".to_owned());
    let client = std::sync::Arc::new(vibe_rs::server::Client::stub());
    vibe_rs::voice_app::open(&mut app);

    let esc = crossterm::event::KeyEvent::new(
        crossterm::event::KeyCode::Esc,
        crossterm::event::KeyModifiers::NONE,
    );
    vibe_rs::voice_app::handle_key(&mut app, &client, esc);

    assert!(!app.voice_app.open);
    assert!(rx.try_recv().is_err());
    assert_eq!(
        app.pending_commits
            .load(std::sync::atomic::Ordering::Relaxed),
        0
    );
}

#[test]
fn reopened_settings_show_the_saved_server_values() {
    let mut app = App::default();

    let runtime = json!({"runtime": {"config": {
        "theme": "ansi-dark",
        "activeModel": {"displayName": "devstral", "thinking": "off"},
        "voiceModeEnabled": true,
        "narratorEnabled": true,
    }}});

    vibe_rs::voice_app::apply_saved(&mut app, &runtime, false, false);
    vibe_rs::voice_app::open(&mut app);

    assert_eq!(app.voice_app.values, [true, true]);
}

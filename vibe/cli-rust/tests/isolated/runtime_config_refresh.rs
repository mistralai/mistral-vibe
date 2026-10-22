//! Config changes reach the live UI: model window, caret blink, mouse capture and theme.

use std::sync::{Arc, Mutex, MutexGuard, Once};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::server::Client;
use vibe_rs::ui::theme;
use vibe_rs::{event_handler, input, model_picker, theme_picker};

/// Guards the active theme, which is process-global across this binary's tests.
static THEME_LOCK: Mutex<()> = Mutex::new(());
static HOME: Once = Once::new();

/// Point the startup cache a full runtime writes at a temp `VIBE_HOME`.
fn isolated() -> MutexGuard<'static, ()> {
    HOME.call_once(|| {
        let home = std::path::Path::new(env!("CARGO_TARGET_TMPDIR")).join("runtime_config_refresh");
        std::fs::create_dir_all(&home).expect("create test home");
        std::env::set_var("VIBE_HOME", home);
    });
    THEME_LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

fn runtime(config: Value, context_window: u64) -> Value {
    let mut base = json!({
        "theme": "ansi-dark",
        "activeModel": {"alias": "medium", "displayName": "Medium", "thinking": "off"},
    });
    base.as_object_mut()
        .unwrap()
        .extend(config.as_object().unwrap().clone());
    json!({"runtime": {"config": base, "contextWindow": context_window}})
}

#[test]
fn a_model_switch_refreshes_the_context_window_max() {
    let _guard = isolated();
    let mut app = App::default();
    event_handler::apply_runtime_value(&mut app, &runtime(json!({}), 600_000));
    assert_eq!(app.session.tokens.1, 600_000);

    let small =
        json!({"activeModel": {"alias": "small", "displayName": "Small", "thinking": "off"}});
    event_handler::apply_runtime_value(&mut app, &runtime(small, 128_000));

    assert_eq!(app.session.tokens.1, 128_000);
}

#[test]
fn a_config_screen_write_turning_cursor_blink_off_steadies_the_caret() {
    let _guard = isolated();
    let mut app = App::default();
    app.view.cursor_on = false;
    assert!(!app.main_input_cursor_on());

    app.commit_started();
    let written = runtime(json!({"cursorBlink": false}), 600_000);
    model_picker::apply_event(&mut app, model_picker::Event::Written(written));

    assert!(app.main_input_cursor_on());
    app.commit_started();
    let written = runtime(json!({"cursorBlink": true}), 600_000);
    model_picker::apply_event(&mut app, model_picker::Event::Written(written));
    assert!(!app.main_input_cursor_on());
}

#[test]
fn a_config_screen_write_toggles_mouse_capture() {
    let _guard = isolated();
    let mut app = App::default();
    assert!(app.session.startup_config.enable_mouse_capture);

    app.commit_started();
    let written = runtime(json!({"enableMouseCapture": false}), 600_000);
    model_picker::apply_event(&mut app, model_picker::Event::Written(written));
    assert!(!app.session.startup_config.enable_mouse_capture);

    app.commit_started();
    let written = runtime(json!({"enableMouseCapture": true}), 600_000);
    model_picker::apply_event(&mut app, model_picker::Event::Written(written));
    assert!(app.session.startup_config.enable_mouse_capture);
}

#[test]
fn a_theme_changed_through_config_applies_live() {
    let _guard = isolated();
    let mut app = App::default();
    theme::set_active("ansi-dark");

    app.commit_started();
    let written = runtime(json!({"theme": "dracula"}), 600_000);
    model_picker::apply_event(&mut app, model_picker::Event::Written(written));

    assert_eq!(theme::active().name, "dracula");
    assert_eq!(app.session.startup_config.theme, "dracula");
}

#[tokio::test]
async fn a_picked_theme_is_applied_before_its_write_completes() {
    let _guard = isolated();
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    app.session.startup_config.theme = "ansi-dark".into();
    theme::set_active("ansi-dark");
    let client = Arc::new(Client::stub());
    theme_picker::open(&mut app);
    let key = |code| KeyEvent::new(code, KeyModifiers::NONE);
    input::handle_theme_key(&mut app, &client, key(KeyCode::Char('j')));
    let picked = theme_picker::selected_name(&app);
    assert_ne!(picked, "ansi-dark");

    input::handle_theme_key(&mut app, &client, key(KeyCode::Enter));

    assert!(!app.theme_picker.open);
    assert_eq!(theme::active().name, picked);
}

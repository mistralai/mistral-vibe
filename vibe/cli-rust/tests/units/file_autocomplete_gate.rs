//! `@` file completion gating: `enable_file_autocomplete` decides the popup.

use vibe_rs::app::App;
use vibe_rs::completion_manager;
use vibe_rs::utils::file_index::FileIndexHandle;
use vibe_rs::utils::startup_cache::StartupConfig;

fn app_with_file_completion(input: &str, enabled: bool) -> App {
    let mut app = App::default();
    app.completion.files = FileIndexHandle::parked(std::path::PathBuf::from("/repo"));
    app.session.startup_config = StartupConfig {
        enable_file_autocomplete: enabled,
        ..Default::default()
    };
    app.chat_input.input = input.into();
    app.chat_input.cursor = input.len();
    completion_manager::input_changed(&mut app);
    app
}

#[test]
fn an_at_token_completes_when_file_autocomplete_is_enabled() {
    let app = app_with_file_completion("@read", true);
    assert!(completion_manager::active_is_file(&app));
}

#[test]
fn an_at_token_never_completes_when_file_autocomplete_is_disabled() {
    let app = app_with_file_completion("@read", false);
    assert!(!completion_manager::active_is_file(&app));
    assert!(!completion_manager::is_open(&app));
}

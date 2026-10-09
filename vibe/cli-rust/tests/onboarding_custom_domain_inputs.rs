//! Custom-domain input behavior: seeding, re-entry reset, paste, and the
//! change-driven feedback row (Python `CustomDomainScreen`).

#[allow(dead_code)]
#[path = "onboarding_common/keys.rs"]
mod keys;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use keys::{enter, esc, type_into};
use vibe_rs::setup::wizard::{screens, Action, OnboardingState, Screen, ValidationState};

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn custom_domain_state() -> OnboardingState {
    let mut state = OnboardingState {
        screen: Screen::SignInTarget,
        ..OnboardingState::default()
    };
    // What `wizard::seed` captures: the launch snapshot and the live
    // context start identical.
    state.config.provider.browser_auth_base_url = Some("https://connector.example".into());
    state.config.provider.browser_auth_api_base_url =
        Some("https://api.connector.example/api".into());
    state.context = state.config.clone();
    state
}

fn open_custom_domain(state: &mut OnboardingState) {
    // Target: choose "Other" (Down + Enter) from the first option; ↓ wraps.
    state.sign_in_target_selected = 0;
    screens::handle_key(state, key(KeyCode::Down));
    assert!(matches!(screens::handle_key(state, enter()), _));
    assert_eq!(state.screen, Screen::CustomDomain);
}

#[test]
fn entering_the_screen_seeds_the_configured_inputs() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);

    assert_eq!(state.custom_domain.value, "https://connector.example");
    assert_eq!(
        state.custom_domain.cursor,
        "https://connector.example".len()
    );
    assert_eq!(
        state.custom_domain_api.value,
        "https://api.connector.example/api"
    );
    // Python suppresses feedback for seeded values: no validation state, no
    // colored borders, no submit hint.
    assert!(matches!(
        state.custom_domain.validation,
        ValidationState::None
    ));
    assert!(matches!(
        state.custom_domain_api.validation,
        ValidationState::None
    ));
    assert_eq!(state.custom_domain_focus, 0);
}

#[test]
fn re_entering_resets_stale_typed_values_to_the_seed() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    // Overwrite the seeded domain with typing.
    state.custom_domain.value = "my.example".into();
    state.custom_domain.cursor = 9;
    state.custom_domain.validation = ValidationState::Valid;
    // Esc back to the target, then re-enter (Python re-runs `_reset_input`).
    screens::handle_key(&mut state, key(KeyCode::Esc));
    assert_eq!(state.screen, Screen::SignInTarget);
    open_custom_domain(&mut state);

    assert_eq!(state.custom_domain.value, "https://connector.example");
    assert!(matches!(
        state.custom_domain.validation,
        ValidationState::None
    ));
}

#[test]
fn backing_out_of_browser_sign_in_never_leaves_the_typo_behind() {
    // The reviewer repro: a typo'd domain starts a sign-in, Esc backs out,
    // and a later API-key path must neither whoami the typo nor pre-fill it.
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    state.custom_domain.value.clear();
    state.custom_domain.cursor = 0;
    type_into(&mut state, "typo.example");
    assert!(matches!(
        screens::handle_key(&mut state, enter()),
        Some(Action::StartBrowserSignIn)
    ));
    assert_eq!(state.screen, Screen::BrowserSignIn);
    assert_eq!(
        state.context.provider.browser_auth_base_url,
        Some("https://typo.example".into())
    );

    assert!(matches!(
        screens::handle_key(&mut state, esc()),
        Some(Action::AbortSignIn)
    ));
    assert_eq!(state.screen, Screen::SignInTarget);
    assert_eq!(state.context, state.config);

    // Re-entering seeds the configured domain again, never the typo.
    open_custom_domain(&mut state);
    assert_eq!(state.custom_domain.value, "https://connector.example");
}

#[test]
fn unconfigured_fields_reset_to_empty_on_reentry() {
    let mut state = OnboardingState {
        screen: Screen::SignInTarget,
        ..OnboardingState::default()
    };
    open_custom_domain(&mut state);
    state.custom_domain.value = "my.example".into();
    screens::handle_key(&mut state, key(KeyCode::Esc));
    open_custom_domain(&mut state);

    assert!(state.custom_domain.value.is_empty());
    assert_eq!(state.custom_domain.cursor, 0);
}

#[test]
fn tab_switches_focus_without_owning_the_feedback_row() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    // Type a valid domain: the feedback row describes the domain input.
    state.custom_domain.value = "my.example".into();
    state.custom_domain.cursor = "my.example".len();
    screens::handle_key(&mut state, key(KeyCode::Char('a')));
    assert_eq!(state.custom_domain_focus, 0);
    assert_eq!(state.custom_domain_feedback, 0);

    // Tab moves the caret but never re-renders the feedback (Python's
    // feedback is change-driven), so the submit hint stays.
    screens::handle_key(&mut state, key(KeyCode::Tab));
    assert_eq!(state.custom_domain_focus, 1);
    assert_eq!(state.custom_domain_feedback, 0);
    // Tab wraps: the last input focuses the first again (Textual focus_next).
    screens::handle_key(&mut state, key(KeyCode::Tab));
    assert_eq!(state.custom_domain_focus, 0);
    assert!(matches!(
        state.custom_domain.validation,
        ValidationState::Valid | ValidationState::Warning(_)
    ));
}

#[test]
fn paste_inserts_into_the_focused_input_and_strips_line_breaks() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    // Select-all-and-type over the seed: clear, then paste with newlines.
    state.custom_domain.value.clear();
    state.custom_domain.cursor = 0;
    screens::paste(&mut state, "my.example\n");
    assert_eq!(state.custom_domain.value, "my.example");
    assert_eq!(state.custom_domain.cursor, "my.example".len());
    assert!(matches!(
        state.custom_domain.validation,
        ValidationState::Valid
    ));

    // Tab to the API base and paste over the cleared field; CR is stripped too.
    screens::handle_key(&mut state, key(KeyCode::Tab));
    state.custom_domain_api.value.clear();
    state.custom_domain_api.cursor = 0;
    screens::paste(&mut state, "https://api.my.example\r\n");
    assert_eq!(state.custom_domain_api.value, "https://api.my.example");
    assert_eq!(state.custom_domain_feedback, 1);
}

#[test]
fn paste_into_the_api_key_input_marks_it_valid() {
    let mut state = OnboardingState {
        screen: Screen::ApiKey,
        ..OnboardingState::default()
    };
    screens::paste(&mut state, "test-key-123\n");
    assert_eq!(state.api_key_input.value, "test-key-123");
    assert!(matches!(
        state.api_key_input.validation,
        ValidationState::Valid
    ));
}

fn ctrl(char: char) -> KeyEvent {
    KeyEvent::new(KeyCode::Char(char), KeyModifiers::CONTROL)
}

#[test]
fn ctrl_u_clears_the_domain_input_like_the_chat_composer() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    state.custom_domain.value.clear();
    state.custom_domain.cursor = 0;
    type_into(&mut state, "my.example");
    assert_eq!(state.custom_domain.value, "my.example");

    screens::handle_key(&mut state, ctrl('u'));
    assert_eq!(state.custom_domain.value, "");
    assert_eq!(state.custom_domain.cursor, 0);
    assert!(matches!(
        state.custom_domain.validation,
        ValidationState::None
    ));
}

#[test]
fn ctrl_w_deletes_the_word_left_of_the_caret() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    state.custom_domain.value.clear();
    state.custom_domain.cursor = 0;
    type_into(&mut state, "my.example");
    screens::handle_key(&mut state, ctrl('w'));
    assert_eq!(state.custom_domain.value, "my.");
    assert_eq!(state.custom_domain.cursor, "my.".len());
}

#[test]
fn ctrl_a_home_end_and_arrows_move_the_caret() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    state.custom_domain.value = "my.example".into();
    state.custom_domain.cursor = "my.example".len();

    screens::handle_key(&mut state, ctrl('a'));
    assert_eq!(state.custom_domain.cursor, 0);
    screens::handle_key(&mut state, key(KeyCode::Right));
    assert_eq!(state.custom_domain.cursor, 1);
    screens::handle_key(&mut state, key(KeyCode::Left));
    assert_eq!(state.custom_domain.cursor, 0);
    screens::handle_key(&mut state, key(KeyCode::End));
    assert_eq!(state.custom_domain.cursor, "my.example".len());
    screens::handle_key(&mut state, key(KeyCode::Home));
    assert_eq!(state.custom_domain.cursor, 0);
    // Ctrl+E mirrors Ctrl+A at the end of the line.
    screens::handle_key(&mut state, ctrl('e'));
    assert_eq!(state.custom_domain.cursor, "my.example".len());
}

#[test]
fn ctrl_u_clears_the_api_key_input_too() {
    let mut state = OnboardingState {
        screen: Screen::ApiKey,
        ..OnboardingState::default()
    };
    for char in "test-key-123".chars() {
        screens::handle_key(&mut state, key(KeyCode::Char(char)));
    }
    assert_eq!(state.api_key_input.value, "test-key-123");
    screens::handle_key(&mut state, ctrl('u'));
    assert_eq!(state.api_key_input.value, "");
    assert!(matches!(
        state.api_key_input.validation,
        ValidationState::None
    ));
}

#[test]
fn enter_still_submits_and_never_inserts_a_newline() {
    let mut state = custom_domain_state();
    open_custom_domain(&mut state);
    state.custom_domain.value.clear();
    state.custom_domain.cursor = 0;
    type_into(&mut state, "my.example");
    let action = screens::handle_key(&mut state, enter());
    assert!(matches!(
        action,
        Some(vibe_rs::setup::wizard::Action::StartBrowserSignIn)
    ));
    assert_eq!(state.screen, Screen::BrowserSignIn);
    // Ctrl+J is the composer's newline insert, but the wizard inputs are
    // single-line: the key does nothing instead of breaking the value.
    let mut single = custom_domain_state();
    open_custom_domain(&mut single);
    single.custom_domain.value.clear();
    single.custom_domain.cursor = 0;
    type_into(&mut single, "my.example");
    screens::handle_key(
        &mut single,
        KeyEvent::new(KeyCode::Char('j'), KeyModifiers::CONTROL),
    );
    assert_eq!(single.custom_domain.value, "my.example");
}

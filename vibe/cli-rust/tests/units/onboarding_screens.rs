//! Onboarding screen routing and input caret semantics (Python
//! `setup/onboarding/__init__.py` and its Textual `Input` widgets).

#[allow(dead_code)]
#[path = "../onboarding_common/keys.rs"]
mod keys;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use keys::{enter, esc};
use ratatui::layout::Rect;

use vibe_rs::app::App;
use vibe_rs::mouse::MouseTarget;
use vibe_rs::setup::auth::rpc::ProviderView;
use vibe_rs::setup::wizard::context::OnboardingContext;
use vibe_rs::setup::wizard::mouse;
use vibe_rs::setup::wizard::screens;
use vibe_rs::setup::wizard::{Action, InputCard, OnboardingState, Screen};

fn theme_state(base_url: &str, api_base_url: &str) -> OnboardingState {
    let context = OnboardingContext::default();
    OnboardingState {
        screen: Screen::ThemeSelection,
        context: OnboardingContext {
            provider: ProviderView {
                browser_auth_base_url: (!base_url.is_empty()).then(|| base_url.into()),
                browser_auth_api_base_url: (!api_base_url.is_empty()).then(|| api_base_url.into()),
                ..context.provider
            },
            // The server's own answer for the seeded provider.
            supports_browser_sign_in: !base_url.is_empty(),
            ..context
        },
        ..OnboardingState::default()
    }
}

#[test]
fn theme_enter_skips_the_auth_method_choice_without_browser_sign_in() {
    let mut state = theme_state("", "");

    let action = screens::handle_key(&mut state, enter());

    assert!(matches!(action, Some(Action::SelectTheme(_))));
    assert_eq!(state.screen, Screen::ApiKey);
}

#[test]
fn theme_enter_keeps_the_auth_method_choice_with_browser_sign_in() {
    let mut state = theme_state(
        "https://console.mistral.ai",
        "https://console.mistral.ai/api",
    );

    screens::handle_key(&mut state, enter());

    assert_eq!(state.screen, Screen::AuthMethod);
}

#[test]
fn clicking_mid_multibyte_input_keeps_the_caret_on_a_char_boundary() {
    let mut app = App::default();
    let mut wizard = OnboardingState {
        screen: Screen::ApiKey,
        ..OnboardingState::default()
    };
    wizard.api_key_input.value = "éabc".into();
    wizard
        .input_rows
        .push((Rect::new(0, 0, 40, 3), InputCard::ApiKey));

    mouse::handle_mouse(
        &mut app,
        &mut wizard,
        MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 4,
            row: 1,
            modifiers: KeyModifiers::NONE,
        },
        MouseTarget::OnboardingInputs,
    );
    // The click lands after the two-byte é: the caret is the byte offset (2),
    // not the char index (1), so the next insert cannot slice mid-character.
    assert_eq!(wizard.api_key_input.cursor, 2);

    screens::handle_key(
        &mut wizard,
        KeyEvent::new(KeyCode::Char('X'), KeyModifiers::NONE),
    );
    assert_eq!(wizard.api_key_input.value, "éXabc");
}

#[test]
fn backspace_after_a_multibyte_paste_deletes_the_whole_character() {
    let mut state = OnboardingState {
        screen: Screen::ApiKey,
        ..OnboardingState::default()
    };

    screens::paste(&mut state, "validé");
    assert_eq!(state.api_key_input.cursor, "validé".len());

    screens::handle_key(
        &mut state,
        KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE),
    );

    assert_eq!(state.api_key_input.value, "valid");
    assert_eq!(state.api_key_input.cursor, 5);
}

#[test]
fn esc_on_theme_goes_back_to_welcome() {
    let mut state = OnboardingState {
        screen: Screen::ThemeSelection,
        ..OnboardingState::default()
    };
    let action = screens::handle_key(&mut state, esc());
    assert!(action.is_none());
    assert_eq!(state.screen, Screen::Welcome);
}

#[test]
fn esc_on_auth_method_goes_back_to_theme() {
    let mut state = OnboardingState {
        screen: Screen::AuthMethod,
        ..OnboardingState::default()
    };
    let action = screens::handle_key(&mut state, esc());
    assert!(action.is_none());
    assert_eq!(state.screen, Screen::ThemeSelection);
}

#[test]
fn esc_on_browser_sign_in_goes_back_to_sign_in_target() {
    let mut state = OnboardingState {
        screen: Screen::BrowserSignIn,
        ..OnboardingState::default()
    };
    state.browser_sign_in.variant = vibe_rs::setup::wizard::SignInVariant::Pending;
    let action = screens::handle_key(&mut state, esc());
    assert!(matches!(action, Some(Action::AbortSignIn)));
    assert_eq!(state.screen, Screen::SignInTarget);
}

#[test]
fn esc_on_browser_sign_in_restores_the_launch_context() {
    // A typo'd domain reached the sign-in screen and mutated the context.
    let mut state = OnboardingState {
        screen: Screen::BrowserSignIn,
        ..OnboardingState::default()
    };
    state.browser_sign_in.variant = vibe_rs::setup::wizard::SignInVariant::Pending;
    state.context.provider.browser_auth_base_url = Some("https://typo.example".into());
    state.context.console_base_url = "https://typo.example".into();

    let action = screens::handle_key(&mut state, esc());
    assert!(matches!(action, Some(Action::AbortSignIn)));
    assert_eq!(state.context, state.config);
}

#[test]
fn the_manual_key_path_keeps_the_attempted_domain() {
    // Python `action_manual`: switching to the API key screen cancels the
    // attempt but keeps the configured domain — the pasted key is for that
    // deployment, so no restore.
    let mut state = OnboardingState {
        screen: Screen::BrowserSignIn,
        ..OnboardingState::default()
    };
    state.browser_sign_in.variant = vibe_rs::setup::wizard::SignInVariant::Pending;
    state.context.provider.browser_auth_base_url = Some("https://console.example".into());

    let action = screens::handle_key(
        &mut state,
        KeyEvent::new(KeyCode::Char('m'), KeyModifiers::NONE),
    );
    assert!(matches!(action, Some(Action::AbortSignIn)));
    assert_eq!(state.screen, Screen::ApiKey);
    assert_eq!(
        state.context.provider.browser_auth_base_url,
        Some("https://console.example".into())
    );
}

#[test]
fn esc_on_welcome_cancels() {
    let mut state = OnboardingState {
        screen: Screen::Welcome,
        welcome_done: true,
        ..OnboardingState::default()
    };
    let action = screens::handle_key(&mut state, esc());
    assert!(matches!(action, Some(Action::Cancel)));
}

#[test]
fn esc_on_sign_in_target_goes_back_to_auth_method() {
    let mut state = OnboardingState {
        screen: Screen::SignInTarget,
        ..OnboardingState::default()
    };
    let action = screens::handle_key(&mut state, esc());
    assert!(action.is_none());
    assert_eq!(state.screen, Screen::AuthMethod);
}

#[test]
fn esc_on_custom_domain_goes_back_to_sign_in_target() {
    let mut state = OnboardingState {
        screen: Screen::CustomDomain,
        ..OnboardingState::default()
    };
    let action = screens::handle_key(&mut state, esc());
    assert!(action.is_none());
    assert_eq!(state.screen, Screen::SignInTarget);
}

#[test]
fn esc_on_api_key_goes_back_to_auth_method_with_browser_sign_in() {
    let mut state = OnboardingState {
        screen: Screen::ApiKey,
        ..OnboardingState::default()
    };
    let action = screens::handle_key(&mut state, esc());
    assert!(action.is_none());
    assert_eq!(state.screen, Screen::AuthMethod);
}

#[test]
fn esc_on_api_key_goes_back_to_theme_selection_without_browser_sign_in() {
    let mut state = OnboardingState {
        screen: Screen::ApiKey,
        ..OnboardingState::default()
    };
    state.context.supports_browser_sign_in = false;
    let action = screens::handle_key(&mut state, esc());
    assert!(action.is_none());
    assert_eq!(state.screen, Screen::ThemeSelection);
}

#[test]
fn ctrl_c_cancels_from_every_screen() {
    for screen in [
        Screen::Welcome,
        Screen::ThemeSelection,
        Screen::AuthMethod,
        Screen::SignInTarget,
        Screen::CustomDomain,
        Screen::BrowserSignIn,
        Screen::ApiKey,
    ] {
        let mut state = OnboardingState {
            screen,
            ..OnboardingState::default()
        };
        let action = screens::handle_key(
            &mut state,
            KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
        );
        assert!(matches!(action, Some(Action::Cancel)), "{screen:?}");
    }
}

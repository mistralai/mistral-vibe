//! The Mistral-default override confirm on the sign-in target screen
//! (Python `SignInTargetScreen.action_select`).

#[allow(dead_code)]
#[path = "onboarding_common/keys.rs"]
mod keys;

use keys::{down, enter};

use vibe_rs::setup::wizard::{screens::handle_key, Action, OnboardingState, Screen};

fn configured_domain_state() -> OnboardingState {
    let mut state = OnboardingState {
        screen: Screen::SignInTarget,
        ..OnboardingState::default()
    };
    // What `wizard::seed` captures: the launch snapshot and the live
    // context start identical.
    state.config.provider.browser_auth_base_url = Some("https://connector.example".into());
    state.config.provider.browser_auth_api_base_url = Some("https://connector.example/api".into());
    state.context = state.config.clone();
    state
}

#[test]
fn overwriting_a_configured_domain_needs_a_second_enter() {
    let mut state = configured_domain_state();

    // The first Enter only arms the warning; nothing is overwritten yet.
    assert!(handle_key(&mut state, enter()).is_none());
    assert!(state.override_confirm_armed);
    assert_eq!(state.screen, Screen::SignInTarget);
    assert_eq!(
        state.context.provider.browser_auth_base_url,
        Some("https://connector.example".into())
    );

    // The second Enter applies the Mistral default and starts sign-in.
    assert!(matches!(
        handle_key(&mut state, enter()),
        Some(Action::StartBrowserSignIn)
    ));
    assert_eq!(state.screen, Screen::BrowserSignIn);
    assert_eq!(
        state.context.provider.browser_auth_base_url,
        Some("https://console.mistral.ai".into())
    );
}

#[test]
fn navigation_disarms_the_override_confirm() {
    let mut state = configured_domain_state();
    assert!(handle_key(&mut state, enter()).is_none());
    assert!(state.override_confirm_armed);

    // Moving the selection disarms; the next Enter re-arms instead of applying.
    assert!(handle_key(&mut state, down()).is_none());
    assert!(!state.override_confirm_armed);
    assert_eq!(state.screen, Screen::SignInTarget);
}

#[test]
fn choosing_other_disarms_and_switches_without_confirm() {
    let mut state = configured_domain_state();
    handle_key(&mut state, down());

    assert!(handle_key(&mut state, enter()).is_none());
    assert_eq!(state.screen, Screen::CustomDomain);
    assert!(!state.override_confirm_armed);
}

#[test]
fn a_default_config_needs_no_confirm() {
    let mut state = OnboardingState {
        screen: Screen::SignInTarget,
        ..OnboardingState::default()
    };

    assert!(matches!(
        handle_key(&mut state, enter()),
        Some(Action::StartBrowserSignIn)
    ));
    assert!(!state.override_confirm_armed);
    assert_eq!(state.screen, Screen::BrowserSignIn);
}

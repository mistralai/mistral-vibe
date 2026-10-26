//! The administrator-pinned console (`VIBE_CUSTOM_CONSOLE_URL` off the
//! server's `setup/status`): the browser sign-in routes to the locked console
//! form, and Enter submits the pinned domain — never a typed one.

use super::onboarding_screens::keys::{down, enter, esc, tab, type_into};
use super::onboarding_seed::status;
use vibe_rs::setup::auth::rpc::SetupStatus;
use vibe_rs::setup::wizard::submit;
use vibe_rs::setup::wizard::{screens, seed, Action, OnboardingState, Screen, ValidationState};

const PIN: &str = "https://console.acme.internal";

/// A wizard seeded from a status that carries the admin pin.
fn pinned_state() -> OnboardingState {
    let mut wizard = OnboardingState::default();
    let status = SetupStatus {
        pinned_console_url: Some(PIN.into()),
        ..status::default_status()
    };
    seed(&mut wizard, &status);
    wizard
}

/// Walk to the locked console form through the auth-method entry point.
fn open_locked_form(state: &mut OnboardingState) {
    state.screen = Screen::AuthMethod;
    assert!(screens::handle_key(state, enter()).is_none());
    assert_eq!(state.screen, Screen::CustomDomain);
}

#[test]
fn seeding_carries_the_pin_into_the_context_and_snapshot() {
    let wizard = pinned_state();
    assert_eq!(wizard.context.pinned_console_url.as_deref(), Some(PIN));
    assert_eq!(wizard.config.pinned_console_url.as_deref(), Some(PIN));
    // The launch snapshot's URLs stay the configured ones: the pin is only
    // applied when the locked form submits.
    assert_eq!(
        wizard.context.provider.browser_auth_base_url.as_deref(),
        Some("https://console.mistral.ai")
    );
}

#[test]
fn auth_method_launch_browser_routes_to_the_locked_form() {
    let mut state = pinned_state();
    open_locked_form(&mut state);

    assert_eq!(state.custom_domain.value, PIN);
    assert_eq!(state.custom_domain.cursor, PIN.len());
    assert!(state.custom_domain_api.value.is_empty());
    assert!(matches!(
        state.custom_domain.validation,
        ValidationState::Valid
    ));
    assert_eq!(state.custom_domain_focus, 0);
    // No drift yet: the pin applies on the form's Enter, not on routing.
    assert_eq!(state.context, state.config);
}

#[test]
fn the_locked_form_ignores_typing_tab_and_paste() {
    let mut state = pinned_state();
    open_locked_form(&mut state);

    type_into(&mut state, "evil.example");
    assert_eq!(state.custom_domain.value, PIN);
    screens::handle_key(&mut state, tab());
    assert_eq!(state.custom_domain_focus, 0);
    screens::paste(&mut state, "https://evil.example\n");
    assert_eq!(state.custom_domain.value, PIN);
    assert!(matches!(
        state.custom_domain.validation,
        ValidationState::Valid
    ));
}

#[test]
fn the_locked_form_enter_submits_the_pinned_domain() {
    let mut state = pinned_state();
    open_locked_form(&mut state);

    assert!(matches!(
        screens::handle_key(&mut state, enter()),
        Some(Action::StartBrowserSignIn)
    ));
    assert_eq!(state.screen, Screen::BrowserSignIn);
    assert_eq!(
        state.context.provider.browser_auth_base_url.as_deref(),
        Some(PIN)
    );
    assert_eq!(
        state.context.provider.browser_auth_api_base_url.as_deref(),
        Some("https://console.acme.internal/api")
    );
    assert!(!state.context.provider.browser_auth_allow_origin_rewrite);
    assert_eq!(state.context.console_base_url, PIN);
}

/// A scheme-less pin (a server that skipped normalization) still derives
/// fully-qualified URLs: `normalize_origin` upgrades the scheme.
#[test]
fn a_scheme_less_pin_still_derives_fully_qualified_urls() {
    let mut wizard = OnboardingState::default();
    let status = SetupStatus {
        pinned_console_url: Some("console.acme.internal".into()),
        ..status::default_status()
    };
    seed(&mut wizard, &status);
    wizard.screen = Screen::AuthMethod;
    assert!(screens::handle_key(&mut wizard, enter()).is_none());
    assert!(matches!(
        screens::handle_key(&mut wizard, enter()),
        Some(Action::StartBrowserSignIn)
    ));
    assert_eq!(
        wizard.context.provider.browser_auth_base_url.as_deref(),
        Some("https://console.acme.internal")
    );
    assert_eq!(
        wizard.context.provider.browser_auth_api_base_url.as_deref(),
        Some("https://console.acme.internal/api")
    );
    assert!(!wizard.context.provider.browser_auth_allow_origin_rewrite);
    assert_eq!(
        wizard.context.console_base_url,
        "https://console.acme.internal"
    );
}

/// A split-horizon API base configured for the pinned console survives the
/// pin: the pin fixes the browser sign-in host, not the connector.
#[test]
fn the_locked_form_enter_preserves_a_split_horizon_api_base() {
    let mut status = status::default_status();
    status.provider.browser_auth_base_url = Some(PIN.into());
    status.provider.browser_auth_api_base_url = Some("https://connector.acme.internal/api".into());
    status.provider.browser_auth_allow_origin_rewrite = true;
    status.pinned_console_url = Some(PIN.into());
    let mut wizard = OnboardingState::default();
    seed(&mut wizard, &status);
    open_locked_form(&mut wizard);

    assert!(matches!(
        screens::handle_key(&mut wizard, enter()),
        Some(Action::StartBrowserSignIn)
    ));
    assert_eq!(
        wizard.context.provider.browser_auth_base_url.as_deref(),
        Some(PIN)
    );
    assert_eq!(
        wizard.context.provider.browser_auth_api_base_url.as_deref(),
        Some("https://connector.acme.internal/api")
    );
    assert!(wizard.context.provider.browser_auth_allow_origin_rewrite);
    // Account calls keep the CLI-reachable connector origin.
    assert_eq!(
        wizard.context.console_base_url,
        "https://connector.acme.internal"
    );
}

#[test]
fn the_locked_form_back_returns_to_auth_method_without_drifting() {
    let mut state = pinned_state();
    open_locked_form(&mut state);

    assert!(screens::handle_key(&mut state, esc()).is_none());
    assert_eq!(state.screen, Screen::AuthMethod);
    assert_eq!(state.context, state.config);
}

#[test]
fn browser_sign_in_back_restores_the_snapshot_and_returns_to_the_locked_form() {
    let mut state = pinned_state();
    open_locked_form(&mut state);
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::BrowserSignIn);

    assert!(matches!(
        screens::handle_key(&mut state, esc()),
        Some(Action::AbortSignIn)
    ));
    assert_eq!(state.screen, Screen::CustomDomain);
    assert_eq!(state.context, state.config);
    // The locked form still shows the pin.
    assert_eq!(state.custom_domain.value, PIN);
}

#[test]
fn submit_choices_carry_the_pinned_urls() {
    let mut state = pinned_state();
    open_locked_form(&mut state);
    screens::handle_key(&mut state, enter());

    let choices = submit::choices(&state);
    assert_eq!(choices.console_base_url.as_deref(), Some(PIN));
    assert_eq!(
        choices.vibe_base_url.as_deref(),
        Some("https://chat.mistral.ai")
    );
    let provider = choices.provider.expect("the provider rides the choices");
    assert_eq!(provider.browser_auth_base_url.as_deref(), Some(PIN));
    assert_eq!(
        provider.browser_auth_api_base_url.as_deref(),
        Some("https://console.acme.internal/api")
    );
}

#[test]
fn the_manual_api_key_path_stays_available_when_pinned() {
    let mut state = pinned_state();
    state.screen = Screen::AuthMethod;
    screens::handle_key(&mut state, down());
    assert!(matches!(
        screens::handle_key(&mut state, enter()),
        Some(Action::AbortSignIn)
    ));
    assert_eq!(state.screen, Screen::ApiKey);
}

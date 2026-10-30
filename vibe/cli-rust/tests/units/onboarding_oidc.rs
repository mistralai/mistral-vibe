//! OIDC opt-in, wire contracts, and credential-free completion.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;
use vibe_rs::setup::auth::oidc::{OidcPoll, OidcStart, OidcStatus};
use vibe_rs::setup::auth::rpc::SetupStatus;
use vibe_rs::setup::auth::sign_in_flow::{SignInEvent, SignInStatus};
use vibe_rs::setup::wizard::context::OnboardingContext;
use vibe_rs::setup::wizard::sign_in::sign_in_actions;
use vibe_rs::setup::wizard::{
    screens, submit, Action, BrowserSignInState, OnboardingState, Screen, SignInVariant,
};

#[test]
fn oidc_start_returns_only_identity_and_poll_accepts_optional_fields() {
    // Producer: app_server/_setup_oidc.py OidcStartResponse and OidcPollResponse.
    let start: OidcStart = serde_json::from_value(json!({"attemptId": "attempt"})).unwrap();
    assert_eq!(start.attempt_id, "attempt");
    for frame in [
        json!({"status": "waiting"}),
        json!({"status": "waiting", "authorizationUrl": null, "detail": null}),
    ] {
        let poll: OidcPoll = serde_json::from_value(frame).unwrap();
        assert_eq!(poll.status, OidcStatus::Waiting);
        assert!(poll.authorization_url.is_none());
        assert!(poll.detail.is_none());
    }
    for (tag, status) in [
        ("waiting", OidcStatus::Waiting),
        ("exchanging", OidcStatus::Exchanging),
        ("completed", OidcStatus::Completed),
        ("failed", OidcStatus::Failed),
    ] {
        let poll: OidcPoll = serde_json::from_value(json!({"status": tag,
            "authorizationUrl": "https://auth.mistral.ai/authorize", "detail": "detail"}))
        .unwrap();
        assert_eq!(poll.status, status);
        assert_eq!(
            poll.authorization_url.as_deref(),
            Some("https://auth.mistral.ai/authorize")
        );
        assert_eq!(poll.detail.as_deref(), Some("detail"));
    }
}

#[test]
fn non_browser_provider_auth_routing_respects_oidc_flag() {
    for enabled in [false, true] {
        let mut state = OnboardingState::default();
        state.context.supports_browser_sign_in = false;
        state.context.oidc_enabled = enabled;
        state.context.provider.name = "internal-ai-gateway".into();
        state.context.provider.browser_auth_base_url = None;
        state.context.provider.browser_auth_api_base_url = None;
        state.config = state.context.clone();
        state.screen = Screen::ThemeSelection;
        let key = |code| KeyEvent::new(code, KeyModifiers::NONE);
        assert!(matches!(
            screens::handle_key(&mut state, key(KeyCode::Enter)),
            Some(Action::SelectTheme(_))
        ));
        assert_eq!(
            state.screen,
            if enabled {
                Screen::AuthMethod
            } else {
                Screen::ApiKey
            }
        );
        if enabled {
            state.auth_method_selected = 1;
            assert!(matches!(
                screens::handle_key(&mut state, key(KeyCode::Enter)),
                Some(Action::AbortSignIn)
            ));
            assert_eq!(state.context.provider, state.config.provider);
        }
        screens::handle_key(&mut state, key(KeyCode::Esc));
        assert_eq!(
            state.screen,
            if enabled {
                Screen::AuthMethod
            } else {
                Screen::ThemeSelection
            }
        );
        if enabled {
            state.auth_method_selected = 0;
            screens::handle_key(&mut state, key(KeyCode::Enter));
            assert_eq!(state.screen, Screen::SignInTarget);
            assert!(matches!(
                screens::handle_key(&mut state, key(KeyCode::Enter)),
                Some(Action::StartBrowserSignIn)
            ));
            assert!(state.context.uses_oidc(true));
            assert_eq!(state.context, state.config);
        }
    }
}

#[test]
fn oidc_mistral_target_does_not_warn_about_config_it_will_preserve() {
    let mut state = OnboardingState::default();
    state.context.oidc_enabled = true;
    state.context.provider.browser_auth_base_url = Some("https://console.custom.invalid".into());
    state.config = state.context.clone();
    state.screen = Screen::SignInTarget;
    assert!(matches!(
        screens::handle_key(
            &mut state,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)
        ),
        Some(Action::StartBrowserSignIn)
    ));
    assert!(!state.override_confirm_armed);
    assert!(state.context.uses_oidc(true));
    assert_eq!(state.context, state.config);
}

#[test]
fn oidc_preserves_inference_config_through_retry_and_manual_entry() {
    for browser_base in [None, Some("https://console.custom.invalid")] {
        let mut state = OnboardingState::default();
        state.context.oidc_enabled = true;
        state.context.provider.name = "custom-gateway".into();
        state.context.provider.api_base = "https://gateway.invalid/v1".into();
        state.context.provider.api_key_env_var = "GATEWAY_API_KEY".into();
        state.context.provider.browser_auth_base_url = browser_base.map(str::to_owned);
        state.context.provider.browser_auth_api_base_url =
            browser_base.map(|base| format!("{base}/api"));
        state.context.console_base_url = "https://console.gateway.invalid".into();
        state.context.vibe_base_url = "https://chat.gateway.invalid".into();
        state.config = state.context.clone();
        state.screen = Screen::SignInTarget;
        let expected = serde_json::to_value(submit::choices(&state)).unwrap();
        let key = |code| KeyEvent::new(code, KeyModifiers::NONE);
        assert!(matches!(
            screens::handle_key(&mut state, key(KeyCode::Enter)),
            Some(Action::StartBrowserSignIn)
        ));
        assert_eq!(state.context, state.config);
        sign_in_actions(
            &mut state.browser_sign_in,
            SignInEvent::Failed {
                message: "Sign-in failed.".into(),
            },
        );
        assert!(matches!(
            screens::handle_key(&mut state, key(KeyCode::Char('r'))),
            Some(Action::RetrySignIn)
        ));
        assert!(state.context.uses_oidc(true));
        assert_eq!(state.context, state.config);
        assert!(matches!(
            screens::handle_key(&mut state, key(KeyCode::Char('m'))),
            Some(Action::AbortSignIn)
        ));
        assert_eq!(state.screen, Screen::ApiKey);
        screens::paste(&mut state, "synthetic-gateway-key");
        assert!(matches!(
            screens::handle_key(&mut state, key(KeyCode::Enter)),
            Some(Action::SubmitApiKey(_))
        ));
        assert_eq!(state.context, state.config);
        assert_eq!(
            serde_json::to_value(submit::choices(&state)).unwrap(),
            expected
        );
    }
}

#[test]
fn oidc_flag_defaults_false_and_is_omitted_for_legacy_status() {
    let frame = super::fixture()["statusResponse"].clone();
    let status: SetupStatus = serde_json::from_value(frame).unwrap();
    assert!(!status.oidc_enabled);
    assert!(serde_json::to_value(status)
        .unwrap()
        .get("oidcEnabled")
        .is_none());
}

#[test]
fn oidc_enabled_round_trips_and_seeds_context() {
    let mut frame = super::fixture()["statusResponse"].clone();
    frame["oidcEnabled"] = json!(true);
    let status: SetupStatus = serde_json::from_value(frame).unwrap();
    assert!(OnboardingContext::from_status(&status).oidc_enabled);
    assert_eq!(serde_json::to_value(status).unwrap()["oidcEnabled"], true);
}

#[test]
fn oidc_completed_submits_choices_not_an_api_key() {
    let mut state = BrowserSignInState::default();
    assert!(sign_in_actions(
        &mut state,
        SignInEvent::StatusChanged(SignInStatus::Completed)
    )
    .is_empty());
    assert_eq!(state.variant, SignInVariant::Success);
    assert_eq!(state.message, "Sign-in complete");
    assert!(matches!(
        sign_in_actions(&mut state, SignInEvent::OidcCompleted).as_slice(),
        [Action::CompleteOidc]
    ));
}

#[test]
fn oidc_discards_abandoned_custom_domain_after_manual_entry() {
    let mut state = OnboardingState::default();
    state.context.oidc_enabled = true;
    state.config = state.context.clone();
    let expected = serde_json::to_value(submit::choices(&state)).unwrap();
    state.screen = Screen::SignInTarget;
    let key = |code| KeyEvent::new(code, KeyModifiers::NONE);
    screens::handle_key(&mut state, key(KeyCode::Down));
    screens::handle_key(&mut state, key(KeyCode::Enter));
    screens::paste(&mut state, "https://abandoned.example");
    assert!(matches!(
        screens::handle_key(&mut state, key(KeyCode::Enter)),
        Some(Action::StartBrowserSignIn)
    ));
    assert_ne!(state.context, state.config);
    assert!(matches!(
        screens::handle_key(&mut state, key(KeyCode::Char('m'))),
        Some(Action::AbortSignIn)
    ));
    screens::handle_key(&mut state, key(KeyCode::Esc));
    screens::handle_key(&mut state, key(KeyCode::Enter));
    screens::handle_key(&mut state, key(KeyCode::Up));
    assert!(matches!(
        screens::handle_key(&mut state, key(KeyCode::Enter)),
        Some(Action::StartBrowserSignIn)
    ));
    assert!(state.context.uses_oidc(state.sign_in_target_selected == 0));
    assert_eq!(state.context, state.config);
    assert_eq!(
        serde_json::to_value(submit::choices(&state)).unwrap(),
        expected
    );
}

#[test]
fn oidc_requires_explicit_mistral_target_without_a_pin() {
    // setup/status flag: app_server/_setup.py; target eligibility is the client choice.
    let mut context = OnboardingContext::default();
    assert!(!context.uses_oidc(true));
    context.oidc_enabled = true;
    assert!(context.uses_oidc(true));
    assert!(!context.uses_oidc(false));
    context.pinned_console_url = Some("https://console.mistral.ai".into());
    assert!(!context.uses_oidc(true));
    context.pinned_console_url = None;
    context.provider.name = "custom".into();
    assert!(context.uses_oidc(true));
    context.provider.name = "mistral".into();
    context.provider.browser_auth_base_url = Some("https://console.acme.internal".into());
    assert!(context.uses_oidc(true));
    context.provider.browser_auth_base_url = None;
    assert!(context.uses_oidc(true));
}

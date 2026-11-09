//! `--setup` with a non-Mistral active provider offers both its API key and the Mistral setup.

use vibe_rs::setup::auth::rpc::{ProviderView, SetupStatus};
use vibe_rs::setup::wizard::{self, screens, Action, OnboardingState, Screen};

use super::onboarding_screens::keys::{down, enter, esc, type_into};
use super::onboarding_seed::status;

fn litellm_status() -> SetupStatus {
    let mistral = status::default_status();
    SetupStatus {
        provider: ProviderView {
            name: "litellm".into(),
            api_base: "http://localhost:4000/v1".into(),
            api_key_env_var: "LITELLM_API_KEY".into(),
            browser_auth_base_url: None,
            browser_auth_api_base_url: None,
            browser_auth_allow_origin_rewrite: false,
        },
        supports_browser_sign_in: false,
        mistral_provider: Some(mistral.provider.clone()),
        ..mistral
    }
}

fn seeded(offers_provider_choice: bool) -> OnboardingState {
    let mut state = OnboardingState {
        offers_provider_choice,
        ..OnboardingState::default()
    };
    wizard::seed(&mut state, &litellm_status());
    state.screen = Screen::ThemeSelection;
    state
}

#[test]
fn setup_offers_the_provider_choice_after_the_theme() {
    let mut state = seeded(true);
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::ProviderChoice);
}

#[test]
fn other_rounds_keep_the_active_provider_api_key_only() {
    let mut state = seeded(false);
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::ApiKey);
    assert_eq!(state.context.provider.name, "litellm");
}

#[test]
fn choosing_mistral_opens_the_browser_sign_in_flow() {
    let mut state = seeded(true);
    state.screen = Screen::ProviderChoice;
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::AuthMethod);
    assert_eq!(state.context.provider.name, "mistral");
    assert!(state.context.supports_browser_sign_in);
    assert_eq!(state.config.provider.name, "mistral");

    screens::handle_key(&mut state, enter());
    let action = screens::handle_key(&mut state, enter());
    assert!(matches!(action, Some(Action::StartBrowserSignIn)));
    assert_eq!(state.screen, Screen::BrowserSignIn);
}

#[test]
fn choosing_the_active_provider_asks_for_its_api_key() {
    let mut state = seeded(true);
    state.screen = Screen::ProviderChoice;
    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::ApiKey);
    assert_eq!(state.context.provider.name, "litellm");
    assert!(!state.context.supports_browser_sign_in);
}

#[test]
fn esc_walks_back_to_the_provider_choice() {
    let mut state = seeded(true);
    state.screen = Screen::ProviderChoice;
    screens::handle_key(&mut state, enter());
    screens::handle_key(&mut state, esc());
    assert_eq!(state.screen, Screen::ProviderChoice);

    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    screens::handle_key(&mut state, esc());
    assert_eq!(state.screen, Screen::ProviderChoice);

    screens::handle_key(&mut state, esc());
    assert_eq!(state.screen, Screen::ThemeSelection);
}

#[test]
fn a_key_typed_for_mistral_is_not_submitted_for_the_other_provider() {
    let mut state = seeded(true);
    state.screen = Screen::ProviderChoice;
    screens::handle_key(&mut state, enter());
    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::ApiKey);
    type_into(&mut state, "mistral-secret");
    screens::handle_key(&mut state, esc());
    screens::handle_key(&mut state, esc());

    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    assert_eq!(state.context.provider.name, "litellm");
    assert!(screens::handle_key(&mut state, enter()).is_none());
}

fn shell_key_status(active: Option<&str>, mistral: Option<&str>) -> SetupStatus {
    SetupStatus {
        active_credential_source: active.map(str::to_owned),
        mistral_active_credential_source: mistral.map(str::to_owned),
        ..litellm_status()
    }
}

fn shell_key_seeded(active: Option<&str>, mistral: Option<&str>) -> OnboardingState {
    let mut state = OnboardingState {
        offers_provider_choice: true,
        ..OnboardingState::default()
    };
    wizard::seed(&mut state, &shell_key_status(active, mistral));
    state.screen = Screen::ProviderChoice;
    state
}

#[test]
fn choosing_mistral_warns_for_its_own_shell_key() {
    let mut state = shell_key_seeded(None, Some("process_env"));
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::AuthMethod);
    assert_eq!(
        state.context.shell_key_override.as_deref(),
        Some("MISTRAL_API_KEY")
    );
}

#[test]
fn choosing_the_active_provider_warns_for_its_own_shell_key() {
    let mut state = shell_key_seeded(Some("process_env"), Some("process_env"));
    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::ApiKey);
    assert_eq!(
        state.context.shell_key_override.as_deref(),
        Some("LITELLM_API_KEY")
    );
}

#[test]
fn backing_out_of_mistral_restores_the_active_providers_warning() {
    let mut state = shell_key_seeded(Some("process_env"), Some("process_env"));
    screens::handle_key(&mut state, enter());
    assert_eq!(
        state.context.shell_key_override.as_deref(),
        Some("MISTRAL_API_KEY")
    );
    screens::handle_key(&mut state, esc());
    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    assert_eq!(
        state.context.shell_key_override.as_deref(),
        Some("LITELLM_API_KEY")
    );
}

#[test]
fn a_choice_without_any_shell_key_warns_on_neither_provider() {
    let mut state = shell_key_seeded(Some("process_env"), None);
    screens::handle_key(&mut state, enter());
    assert_eq!(state.context.shell_key_override, None);
    screens::handle_key(&mut state, esc());
    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    assert_eq!(
        state.context.shell_key_override.as_deref(),
        Some("LITELLM_API_KEY")
    );
}

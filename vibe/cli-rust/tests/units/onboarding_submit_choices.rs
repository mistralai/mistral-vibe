//! The wizard's `setup/submit-choices` payload (Python
//! `persist_credentials` plus the post-exit `/theme` write): the full
//! provider and base-URL choices on every completion — the server judges
//! drift — and the theme whenever one was selected, never when none was.

use serde_json::json;

use vibe_rs::setup::wizard::submit;
use vibe_rs::setup::wizard::OnboardingState;

fn custom_domain_state() -> OnboardingState {
    let mut state = OnboardingState::default();
    state.context.provider.api_base = "https://api.globalaegis.net/v1".into();
    state.context.provider.browser_auth_base_url = Some("https://console.globalaegis.net".into());
    state.context.provider.browser_auth_api_base_url =
        Some("https://console.globalaegis.net/api".into());
    state.context.provider.browser_auth_allow_origin_rewrite = true;
    state.context.console_base_url = "https://console.globalaegis.net".into();
    state
}

#[test]
fn the_provider_view_rides_the_wire_as_the_six_field_camelcase_frame() {
    let state = custom_domain_state();
    let choices = submit::choices(&state);
    let provider = choices.provider.expect("provider on every completion");
    // The app-server merges the view onto the full resolved provider, so
    // `backend` and friends never ride the wire. The wire is camelCase.
    assert_eq!(
        json!({
            "name": "mistral",
            "apiBase": "https://api.globalaegis.net/v1",
            "apiKeyEnvVar": "MISTRAL_API_KEY",
            "browserAuthBaseUrl": "https://console.globalaegis.net",
            "browserAuthApiBaseUrl": "https://console.globalaegis.net/api",
            "browserAuthAllowOriginRewrite": true,
        }),
        serde_json::to_value(&provider).expect("view serializes")
    );
    assert_eq!(
        choices.console_base_url.as_deref(),
        Some("https://console.globalaegis.net")
    );
    assert_eq!(
        choices.vibe_base_url.as_deref(),
        Some("https://chat.mistral.ai")
    );
    assert_eq!(choices.theme, None);
}

#[test]
fn the_base_urls_always_submit_with_the_provider() {
    let mut state = OnboardingState::default();
    state.context.vibe_base_url = "https://vibe.globalaegis.net".into();
    let choices = submit::choices(&state);
    assert!(
        choices.provider.is_some(),
        "the provider rides every submit"
    );
    assert_eq!(
        choices.vibe_base_url.as_deref(),
        Some("https://vibe.globalaegis.net")
    );
    assert_eq!(
        choices.console_base_url.as_deref(),
        Some("https://console.mistral.ai")
    );
}

#[test]
fn a_theme_is_submitted_whenever_one_was_selected() {
    // Python persists /theme on every completed onboarding that selected
    // one — even the seeded value, not only a moved selection.
    let mut state = OnboardingState {
        theme_selected: true,
        ..OnboardingState::default()
    };
    assert_eq!(submit::choices(&state).theme, Some("auto".into()));
    state.theme_index = 1;
    assert_eq!(
        submit::choices(&state).theme,
        Some(vibe_rs::theme_picker::options()[1].to_owned())
    );
}

#[test]
fn an_unselected_theme_is_never_submitted() {
    let state = OnboardingState::default();
    let choices = submit::choices(&state);
    assert_eq!(choices.theme, None, "no selection, no write signal");
}

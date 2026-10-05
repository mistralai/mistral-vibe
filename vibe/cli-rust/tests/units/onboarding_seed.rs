//! Wizard seeding from the server's resolved view (`setup/status`), the
//! Python `OnboardingContext` equivalent: the server owns the config read;
//! the client keeps only the `VIBE_THEME` override (env wins over config),
//! and the browser-sign-in answer comes off the wire, not a URL-presence
//! guess.

#[path = "../onboarding_common/status.rs"]
pub(crate) mod status;

use vibe_rs::setup::auth::rpc::{ProviderView, SetupStatus};
use vibe_rs::setup::wizard::{self, OnboardingState};

fn status() -> SetupStatus {
    // The shared mistral default, with the theme the seeding test varies.
    SetupStatus {
        theme: "ansi-dark".into(),
        ..status::default_status()
    }
}

#[test]
fn seeding_adopts_the_server_view() {
    let mut wizard = OnboardingState::default();
    wizard::seed(&mut wizard, &status());
    assert_eq!(wizard.context.provider.name, "mistral");
    assert_eq!(
        wizard.context.provider.api_base,
        "https://api.mistral.ai/v1"
    );
    assert_eq!(wizard.context.theme, "ansi-dark");
    assert_eq!(
        wizard.context.console_base_url,
        "https://console.mistral.ai"
    );
    // The server's browser-sign-in answer is adopted as-is.
    assert!(wizard.context.supports_browser_sign_in);
    // The theme index follows the server's theme.
    assert_eq!(
        wizard.theme_index,
        vibe_rs::theme_picker::options()
            .iter()
            .position(|theme| *theme == "ansi-dark")
            .expect("ansi-dark theme")
    );
}

#[test]
fn a_provider_without_auth_urls_maps_to_the_wizard_empty_state() {
    let status = SetupStatus {
        provider: ProviderView {
            name: "llamacpp".into(),
            api_base: "http://127.0.0.1:8080/v1".into(),
            api_key_env_var: String::new(),
            browser_auth_base_url: None,
            browser_auth_api_base_url: None,
            browser_auth_allow_origin_rewrite: false,
        },
        theme: "auto".into(),
        supports_browser_sign_in: false,
        ..status()
    };
    let mut wizard = OnboardingState::default();
    wizard::seed(&mut wizard, &status);
    assert!(wizard.context.provider.browser_auth_base_url.is_none());
    assert!(!wizard.context.supports_browser_sign_in);
}

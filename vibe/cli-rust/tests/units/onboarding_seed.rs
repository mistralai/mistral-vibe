//! Wizard seeding from the server's resolved view (`setup/status`): the
//! server owns the config read; the client keeps only the `VIBE_THEME`
//! override (env wins over config), and the browser-sign-in answer comes
//! off the wire, not a URL-presence guess.

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

/// `std::env::set_var` is process-global: the locale tests serialize on the
/// same lock the credentials and proxy tests mutate env through.
use super::setup_probe::ENV_LOCK;

const LOCALE_VARS: [&str; 4] = ["VIBE_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG"];

fn locales(entries: &[(&str, &str)]) -> std::collections::HashMap<String, String> {
    entries
        .iter()
        .map(|(tag, text)| ((*tag).to_owned(), (*text).to_owned()))
        .collect()
}

fn seeded_with_locales(
    locales: std::collections::HashMap<String, String>,
) -> wizard::OnboardingState {
    let status = SetupStatus {
        setup_text_locales: Some(locales),
        ..status::default_status()
    };
    let mut wizard = OnboardingState::default();
    wizard::seed(&mut wizard, &status);
    wizard
}

#[test]
fn a_plugin_setup_text_replaces_the_welcome_text() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    for var in LOCALE_VARS {
        std::env::remove_var(var);
    }
    let wizard = seeded_with_locales(locales(&[("en", "Welcome to Acme!")]));
    assert_eq!(wizard.context.welcome_text, "Welcome to Acme!");
    assert_eq!(
        wizard.context.setup_text_locales.as_ref().expect("locales")["en"],
        "Welcome to Acme!"
    );
}

#[test]
fn the_welcome_text_resolves_the_user_locale_exactly_then_by_language() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    for var in LOCALE_VARS {
        std::env::remove_var(var);
    }
    let map = locales(&[("en", "Welcome to Acme!"), ("fr", "Bienvenue !")]);
    // An exact regional tag beats the language-only entry.
    std::env::set_var("VIBE_LOCALE", "fr-CA");
    let wizard = seeded_with_locales(map.clone());
    assert_eq!(wizard.context.welcome_text, "Bienvenue !");
    // The `LANG` chain normalizes (`fr_FR.UTF-8` -> `fr-fr`).
    std::env::remove_var("VIBE_LOCALE");
    std::env::set_var("LANG", "fr_FR.UTF-8");
    let wizard = seeded_with_locales(map.clone());
    assert_eq!(wizard.context.welcome_text, "Bienvenue !");
    // An unmatched locale falls back to the mandatory `en` entry.
    std::env::set_var("LANG", "de_DE.UTF-8");
    let wizard = seeded_with_locales(map.clone());
    assert_eq!(wizard.context.welcome_text, "Welcome to Acme!");
    // `C` is not a locale: a candidate that misses moves to the next one.
    std::env::set_var("LC_ALL", "C");
    let wizard = seeded_with_locales(map);
    assert_eq!(wizard.context.welcome_text, "Welcome to Acme!");
    std::env::remove_var("LC_ALL");
    std::env::remove_var("LANG");
}

#[test]
fn no_plugin_setup_text_keeps_the_default_welcome_text() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    for var in LOCALE_VARS {
        std::env::remove_var(var);
    }
    let status = SetupStatus {
        setup_text_locales: None,
        ..status::default_status()
    };
    let mut wizard = OnboardingState::default();
    wizard::seed(&mut wizard, &status);
    assert_eq!(wizard.context.welcome_text, wizard::WELCOME_TEXT);
    assert!(wizard.context.setup_text_locales.is_none());
}

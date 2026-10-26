//! The custom-domain screen: input handling, live validation, and the
//! Mistral-default reset.

use crossterm::event::{KeyCode, KeyEvent};

use super::context::{
    browser_auth_account_base, browser_auth_requires_origin_rewrite, configured_custom_api_base,
    configured_custom_domain, default_provider, is_likely_mistral_private_cloud_domain,
    is_valid_custom_domain, pinned_console_api_base, resolve_browser_auth_urls, OnboardingContext,
    DEFAULT_CONSOLE_BASE_URL, DEFAULT_VIBE_BASE_URL,
};
use super::screens::edit_key;
use super::{Action, OnboardingState, Screen, ValidationState};

pub(super) fn handle_custom_domain(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    // An administrator-pinned console renders locked: the only moves are
    // Enter (start the sign-in) and Esc (back to the auth method).
    if let Some(pin) = state.context.pinned_console_url.clone() {
        return match key.code {
            KeyCode::Esc => {
                state.screen = Screen::AuthMethod;
                None
            }
            KeyCode::Enter => {
                // A split-horizon API base configured for the pinned console
                // (read from the launch snapshot) is preserved; otherwise the
                // API base derives from the pin.
                let api_base = pinned_console_api_base(&state.config.provider, &pin);
                apply_custom_domain(&mut state.context, &pin, api_base.as_deref());
                state.screen = Screen::BrowserSignIn;
                Some(Action::StartBrowserSignIn)
            }
            _ => None,
        };
    }
    match key.code {
        KeyCode::Esc => {
            state.screen = Screen::SignInTarget;
            None
        }
        // Tab moves focus between the two inputs and wraps: the last input
        // focuses the first again. Shift+Tab
        // arrives as BackTab, so one arm covers both.
        KeyCode::Tab | KeyCode::BackTab => {
            state.custom_domain_focus = (state.custom_domain_focus + 1) % 2;
            None
        }
        KeyCode::Enter => {
            let domain = state.custom_domain.value.trim().to_owned();
            let api_base = state.custom_domain_api.value.trim().to_owned();
            if !is_valid_custom_domain(&domain) {
                state.custom_domain.validation =
                    ValidationState::Invalid("Enter a valid domain URL.".into());
                state.custom_domain_feedback = 0;
                None
            } else if !api_base.is_empty() && !is_valid_custom_domain(&api_base) {
                state.custom_domain_api.validation =
                    ValidationState::Invalid("Enter a valid API base URL.".into());
                state.custom_domain_feedback = 1;
                None
            } else {
                apply_custom_domain(&mut state.context, &domain, Some(&api_base));
                state.screen = Screen::BrowserSignIn;
                Some(Action::StartBrowserSignIn)
            }
        }
        _ => {
            let handled = {
                let input = state.focused_custom_domain_input();
                edit_key(input, &key)
            };
            if handled {
                validate_custom_domain_input(state);
            }
            None
        }
    }
}

/// Reset the custom-domain inputs to the configured seeds (on every mount and
/// resume): the domain, and only a distinct split-horizon API base. This reads
/// the launch snapshot, so a domain the wizard configured and then backed
/// out of never pre-fills. Unconfigured fields reset to empty, both
/// validations drop to `None` so a seeded screen shows no feedback and no
/// colored borders, and focus returns to the domain.
pub(super) fn reset_custom_domain_inputs(state: &mut OnboardingState) {
    // A pinned console seeds and locks the domain; the API base input stays
    // empty (a split base configured for the pin is preserved on Enter) and
    // the card shows valid, so the hint paints.
    if let Some(pin) = state.context.pinned_console_url.clone() {
        state.custom_domain.value = pin;
        state.custom_domain.cursor = state.custom_domain.value.len();
        state.custom_domain.validation = ValidationState::Valid;
        state.custom_domain_api.value = String::new();
        state.custom_domain_api.cursor = 0;
        state.custom_domain_api.validation = ValidationState::None;
        state.custom_domain_focus = 0;
        state.custom_domain_feedback = 0;
        return;
    }
    let domain = configured_custom_domain(&state.config.provider).unwrap_or_default();
    let api = configured_custom_api_base(&state.config.provider).unwrap_or_default();
    state.custom_domain.value = domain;
    state.custom_domain.cursor = state.custom_domain.value.len();
    state.custom_domain.validation = ValidationState::None;
    state.custom_domain_api.value = api;
    state.custom_domain_api.cursor = state.custom_domain_api.value.len();
    state.custom_domain_api.validation = ValidationState::None;
    state.custom_domain_focus = 0;
    state.custom_domain_feedback = 0;
}

pub(super) fn validate_custom_domain_input(state: &mut OnboardingState) {
    // The edited (focused) input owns the feedback row; switching focus
    // never re-renders it (feedback is change-driven).
    state.custom_domain_feedback = state.custom_domain_focus;
    let value = state.custom_domain.value.trim().to_owned();
    state.custom_domain.validation = if value.is_empty() {
        ValidationState::None
    } else if is_valid_custom_domain(&value) {
        if is_likely_mistral_private_cloud_domain(&value) {
            ValidationState::Warning(
                "Mistral private-cloud domain detected. For Mistral-hosted accounts, \
                 use console.mistral.ai. Proceed only for self-hosted deployments."
                    .into(),
            )
        } else {
            ValidationState::Valid
        }
    } else {
        ValidationState::Invalid("Enter a valid domain URL.".into())
    };
    let api = state.custom_domain_api.value.trim().to_owned();
    state.custom_domain_api.validation = if api.is_empty() {
        ValidationState::None
    } else if is_valid_custom_domain(&api) {
        ValidationState::Valid
    } else {
        ValidationState::Invalid("Enter a valid API base URL.".into())
    };
}

/// Reset to the default Mistral domain:
/// the provider URLs, the rewrite flag, and the account/vibe bases — all
/// rebuilt from the same defaults `default_provider()` uses, so the wizard
/// and the config layer cannot drift.
pub(super) fn apply_mistral_default(context: &mut OnboardingContext) {
    let provider = default_provider();
    context.provider.browser_auth_base_url = provider.browser_auth_base_url;
    context.provider.browser_auth_api_base_url = provider.browser_auth_api_base_url;
    context.provider.browser_auth_allow_origin_rewrite = provider.browser_auth_allow_origin_rewrite;
    context.provider.api_base = provider.api_base;
    context.console_base_url = DEFAULT_CONSOLE_BASE_URL.into();
    context.vibe_base_url = DEFAULT_VIBE_BASE_URL.into();
}

/// Apply a custom domain: the provider's browser-auth URLs and the account
/// base derive from it (an empty API base means the console's own `/api`).
/// The administrator pin rides the same path, minus the choice of domain.
fn apply_custom_domain(context: &mut OnboardingContext, domain: &str, api_base_url: Option<&str>) {
    let (browser_base, api) = resolve_browser_auth_urls(domain, api_base_url);
    context.provider.browser_auth_base_url = Some(browser_base.clone());
    context.provider.browser_auth_api_base_url = Some(api.clone());
    context.provider.browser_auth_allow_origin_rewrite =
        browser_auth_requires_origin_rewrite(&browser_base, &api);
    // In a split-horizon setup the browser console origin is not
    // CLI-reachable, so account calls move to the API base origin.
    context.console_base_url = browser_auth_account_base(&browser_base, Some(&api));
}

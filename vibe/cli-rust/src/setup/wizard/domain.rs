//! The custom-domain screen: input handling, live validation, and the
//! Mistral-default reset (Python `CustomDomainScreen`).

use crossterm::event::{KeyCode, KeyEvent};

use super::context::{
    browser_auth_account_base, browser_auth_requires_origin_rewrite, configured_custom_api_base,
    configured_custom_domain, default_provider, is_likely_mistral_private_cloud_domain,
    is_valid_custom_domain, resolve_browser_auth_urls, OnboardingContext, DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_VIBE_BASE_URL,
};
use super::screens::edit_key;
use super::{Action, OnboardingState, Screen, ValidationState};

pub(super) fn handle_custom_domain(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Esc => {
            state.screen = Screen::SignInTarget;
            None
        }
        // Python moves focus between the two inputs with Textual's Tab,
        // which wraps: the last input focuses the first again. Shift+Tab
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
                let (browser_base, api) =
                    resolve_browser_auth_urls(&domain, (!api_base.is_empty()).then_some(&api_base));
                state.context.provider.browser_auth_base_url = Some(browser_base.clone());
                state.context.provider.browser_auth_api_base_url = Some(api.clone());
                state.context.provider.browser_auth_allow_origin_rewrite =
                    browser_auth_requires_origin_rewrite(&browser_base, &api);
                // In a split-horizon setup the browser console origin is not
                // CLI-reachable, so account calls move to the API base origin.
                state.context.console_base_url = browser_auth_account_base(
                    &browser_base,
                    (!api_base.is_empty()).then_some(&api),
                );
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

/// Reset the custom-domain inputs to the configured seeds (Python
/// `CustomDomainScreen._reset_input`, run on every mount and resume): the
/// domain, and only a distinct split-horizon API base. Python reads the
/// launch snapshot here, so a domain the wizard configured and then backed
/// out of never pre-fills. Unconfigured fields reset to empty, both
/// validations drop to `None` so a seeded screen shows no feedback and no
/// colored borders, and focus returns to the domain.
pub(super) fn reset_custom_domain_inputs(state: &mut OnboardingState) {
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
    // never re-renders it (Python's feedback is change-driven).
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

/// Reset to the default Mistral domain (Python `apply_mistral_default_domain`):
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

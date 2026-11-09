//! `--setup`'s provider choice: a non-Mistral active provider also gets the
//! Mistral setup (browser sign-in or Mistral API key).

use crossterm::event::{KeyCode, KeyEvent};

use super::context::shell_override_env_var;
use super::{Action, OnboardingInput, OnboardingState, Screen};
use crate::setup::auth::rpc::{ProviderView, SetupStatus};

/// The two providers offered when the active one has no browser sign-in.
#[derive(Debug, Clone)]
pub struct ProviderChoice {
    pub mistral: ProviderView,
    pub active: ProviderView,
    /// The Mistral provider's key env var when the shell sets it: the wizard
    /// warns for the provider the user picks, never the seeded one's.
    pub mistral_shell_key_override: Option<String>,
    /// The active provider's, as the seed computed it: re-applied when the
    /// user backs out of a Mistral choice.
    pub active_shell_key_override: Option<String>,
}

/// The choice the server's status allows, when the round offers one.
pub(super) fn from_status(state: &OnboardingState, status: &SetupStatus) -> Option<ProviderChoice> {
    let mistral = status.mistral_provider.clone()?;
    state.offers_provider_choice.then(|| ProviderChoice {
        mistral_shell_key_override: shell_override_env_var(
            status.mistral_active_credential_source.as_deref(),
            &mistral,
        ),
        active_shell_key_override: shell_override_env_var(
            status.active_credential_source.as_deref(),
            &status.provider,
        ),
        mistral,
        active: status.provider.clone(),
    })
}

/// Point the wizard at one of the offered providers. The snapshot moves too,
/// so the screens after behave as if the server had seeded that provider.
pub fn choose(state: &mut OnboardingState, mistral: bool) {
    let Some(choice) = &state.provider_choice else {
        return;
    };
    let provider = if mistral {
        choice.mistral.clone()
    } else {
        choice.active.clone()
    };
    // A key typed for the other provider must never be submitted for this one.
    if provider.name != state.config.provider.name {
        state.api_key_input = OnboardingInput::default();
    }
    state.config.provider = provider;
    state.config.supports_browser_sign_in = mistral;
    state.config.shell_key_override = if mistral {
        choice.mistral_shell_key_override.clone()
    } else {
        choice.active_shell_key_override.clone()
    };
    state.context = state.config.clone();
}

pub(super) fn handle_key(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Up | KeyCode::Char('k') | KeyCode::Down | KeyCode::Char('j') => {
            let down = matches!(key.code, KeyCode::Down | KeyCode::Char('j'));
            state.provider_selected = crate::list_nav::wrap(state.provider_selected, 2, down);
        }
        KeyCode::Enter => {
            let mistral = state.provider_selected == 0;
            choose(state, mistral);
            state.screen = if mistral {
                Screen::AuthMethod
            } else {
                Screen::ApiKey
            };
        }
        KeyCode::Esc => state.screen = Screen::ThemeSelection,
        _ => {}
    }
    None
}

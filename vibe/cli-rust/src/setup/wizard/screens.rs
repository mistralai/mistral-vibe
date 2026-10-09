//! Per-screen key handling for the onboarding wizard.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::context::{configured_custom_api_base, configured_custom_domain};
use super::domain::{
    apply_mistral_default, handle_custom_domain, reset_custom_domain_inputs,
    validate_custom_domain_input,
};
use super::{Action, OnboardingInput, OnboardingState, Screen, ValidationState};
use crate::ui::theme;
use crate::utils::input_edit;

/// Handle a key press for the current screen. Returns an action to take, or
/// `None` when the key only moved wizard state (Python's handlers are void).
pub fn handle_key(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        return Some(Action::Cancel);
    }
    match state.screen {
        Screen::Welcome => handle_welcome(state, key),
        Screen::ThemeSelection => handle_theme(state, key),
        Screen::AuthMethod => handle_auth_method(state, key),
        Screen::SignInTarget => handle_sign_in_target(state, key),
        Screen::CustomDomain => handle_custom_domain(state, key),
        Screen::BrowserSignIn => handle_browser_sign_in(state, key),
        Screen::ApiKey => handle_api_key(state, key),
    }
}

fn handle_welcome(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    if state.welcome_done && key.code == KeyCode::Enter {
        state.screen = Screen::ThemeSelection;
        return None;
    }
    if key.code == KeyCode::Esc {
        return Some(Action::Cancel);
    }
    None
}

fn handle_theme(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Up | KeyCode::Down | KeyCode::Char('j' | 'k') => {
            step_theme(state, matches!(key.code, KeyCode::Up | KeyCode::Char('k')));
            None
        }
        KeyCode::Enter => {
            // Python `theme_next`: providers without browser-auth URLs
            // (llama.cpp / local) skip the auth-method choice entirely.
            state.screen = if state.context.supports_browser_sign_in {
                Screen::AuthMethod
            } else {
                Screen::ApiKey
            };
            Some(Action::SelectTheme(state.theme_index))
        }
        KeyCode::Esc => {
            state.screen = Screen::Welcome;
            None
        }
        _ => None,
    }
}

/// Move the selection one option with wraparound, shared by the arrow keys
/// and the wheel (Python's pickers navigate one option at a time).
pub(crate) fn step_theme(state: &mut OnboardingState, up: bool) {
    let total = crate::theme_picker::options().len().max(1);
    let step = if up { total - 1 } else { 1 };
    select_theme(state, state.theme_index + step);
}

/// Move the selection to `index` (wrapping) and repaint the wizard in that
/// theme; shared by the arrow keys, the wheel, and mouse clicks.
pub(crate) fn select_theme(state: &mut OnboardingState, index: usize) {
    let themes = crate::theme_picker::options().len();
    state.theme_index = index % themes.max(1);
    preview_selected_theme(state);
}

/// Repaint the wizard in the highlighted theme (Python `self.app.theme = resolve_theme`).
fn preview_selected_theme(state: &OnboardingState) {
    if let Some(name) = crate::theme_picker::options().get(state.theme_index) {
        if let Some(index) = theme::resolve(name) {
            theme::set_active_index(index);
        }
    }
}

fn handle_auth_method(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Up | KeyCode::Char('k') | KeyCode::Down | KeyCode::Char('j') => {
            let down = matches!(key.code, KeyCode::Down | KeyCode::Char('j'));
            state.auth_method_selected = crate::list_nav::wrap(state.auth_method_selected, 2, down);
            None
        }
        KeyCode::Enter => {
            if state.auth_method_selected == 0 {
                state.screen = Screen::SignInTarget;
                None
            } else {
                state.screen = Screen::ApiKey;
                Some(Action::AbortSignIn)
            }
        }
        KeyCode::Esc => {
            state.screen = Screen::ThemeSelection;
            None
        }
        _ => None,
    }
}

fn handle_sign_in_target(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Up | KeyCode::Char('k') | KeyCode::Down | KeyCode::Char('j') => {
            let down = matches!(key.code, KeyCode::Down | KeyCode::Char('j'));
            state.override_confirm_armed = false;
            let selected = state.sign_in_target_selected;
            state.sign_in_target_selected = crate::list_nav::wrap(selected, 2, down);
            None
        }
        KeyCode::Enter => {
            if state.sign_in_target_selected == 0 {
                // Python `action_select`: switching to the Mistral default
                // overwrites a configured custom domain, so the first Enter
                // only arms a warning and the second one proceeds. Python
                // reads the immutable launch snapshot here — never a domain
                // the wizard itself configured and then backed out of.
                let configured = configured_custom_domain(&state.config.provider)
                    .or_else(|| configured_custom_api_base(&state.config.provider));
                if let Some(domain) = configured.filter(|_| !state.override_confirm_armed) {
                    state.override_confirm_domain = domain;
                    state.override_confirm_armed = true;
                    return None;
                }
                state.override_confirm_armed = false;
                apply_mistral_default(&mut state.context);
                state.screen = Screen::BrowserSignIn;
                Some(Action::StartBrowserSignIn)
            } else {
                state.override_confirm_armed = false;
                reset_custom_domain_inputs(state);
                state.screen = Screen::CustomDomain;
                None
            }
        }
        KeyCode::Esc => {
            state.override_confirm_armed = false;
            state.screen = Screen::AuthMethod;
            None
        }
        _ => None,
    }
}

/// Run one key through the chat input's own keymap and edit pipeline, so every
/// wizard input binds exactly what the composer binds (Ctrl+U, Ctrl+W, word
/// deletes, Home/End, arrows, Delete). The wizard inputs are single-line, so
/// the newline inserts are dropped; selection actions run too but their anchor
/// is discarded, leaving just the caret move.
pub(super) fn edit_key(input: &mut OnboardingInput, key: &KeyEvent) -> bool {
    let Some(action) = crate::keymap::action_for(key) else {
        return false;
    };
    if matches!(action, crate::chat_input::Action::Insert('\n')) {
        return false;
    }
    let mut anchor = None;
    crate::chat_input::apply(&action, &mut input.value, &mut input.cursor, &mut anchor);
    true
}

fn handle_browser_sign_in(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Char('r') if state.browser_sign_in.variant == super::SignInVariant::Error => {
            Some(Action::RetrySignIn)
        }
        KeyCode::Char('c') if state.browser_sign_in.sign_in_url.is_some() => {
            Some(Action::CopySignInUrl)
        }
        KeyCode::Char('m') if state.browser_sign_in.variant != super::SignInVariant::Success => {
            state.screen = Screen::ApiKey;
            Some(Action::AbortSignIn)
        }
        KeyCode::Esc if state.browser_sign_in.variant != super::SignInVariant::Success => {
            // Backing out abandons the attempted domain: the context returns
            // to the launch snapshot, so a later API-key submission cannot
            // whoami the abandoned host (Python's Esc exits the whole wizard
            // instead; the 'm' manual path below keeps the domain).
            state.context = state.config.clone();
            state.screen = Screen::SignInTarget;
            Some(Action::AbortSignIn)
        }
        _ => None,
    }
}

fn handle_api_key(state: &mut OnboardingState, key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Esc => {
            state.screen = if state.context.supports_browser_sign_in {
                Screen::AuthMethod
            } else {
                Screen::ThemeSelection
            };
            None
        }
        KeyCode::Enter => {
            let key = state.api_key_input.value.trim().to_owned();
            if !key.is_empty() {
                Some(Action::SubmitApiKey(key))
            } else {
                state.api_key_input.validation =
                    ValidationState::Invalid("No API key provided.".into());
                None
            }
        }
        _ => {
            if edit_key(&mut state.api_key_input, &key) {
                validate_api_key_input(&mut state.api_key_input);
            }
            None
        }
    }
}

/// The API key input's live validation: empty is unvalidated, anything
/// else is valid (Python validates only on submit).
fn validate_api_key_input(input: &mut OnboardingInput) {
    input.validation = if input.value.is_empty() {
        ValidationState::None
    } else {
        ValidationState::Valid
    };
}

/// Paste into the focused wizard input (Python's Textual `Input` paste). The
/// inputs are single-line, so line breaks are dropped; the custom-domain
/// inputs revalidate exactly like a typed character, and the API key input
/// marks itself valid exactly like typing.
pub fn paste(state: &mut OnboardingState, text: &str) {
    let text: String = text.chars().filter(|c| *c != '\n' && *c != '\r').collect();
    if text.is_empty() {
        return;
    }
    match state.screen {
        Screen::CustomDomain => {
            let input = state.focused_custom_domain_input();
            input_edit::insert(&mut input.value, &mut input.cursor, &text);
            validate_custom_domain_input(state);
        }
        Screen::ApiKey => {
            input_edit::insert(
                &mut state.api_key_input.value,
                &mut state.api_key_input.cursor,
                &text,
            );
            validate_api_key_input(&mut state.api_key_input);
        }
        _ => {}
    }
}

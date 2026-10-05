//! The onboarding wizard's state and screen routing (Python
//! `vibe/setup/onboarding`). Owned by the wizard flow, never by the chat
//! app: the TUI runs only after the wizard closed.

pub mod actions;
pub mod boot;
pub mod context;
mod domain;
pub mod flow;
pub mod mouse;
pub mod paint;
pub mod screens;
pub mod sign_in;
pub mod submit;

pub use self::sign_in::{BrowserSignInState, SignInStep, SignInVariant};

use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent};

use self::context::OnboardingContext;
use crate::setup::auth::rpc::SetupStatus;

/// The welcome screen's typing-animation text (Python's onboarding welcome).
pub const WELCOME_TEXT: &str = "Welcome to Mistral Vibe - Let's get you started!";

/// Which wizard screen is active.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Screen {
    Welcome,
    ThemeSelection,
    AuthMethod,
    SignInTarget,
    CustomDomain,
    BrowserSignIn,
    ApiKey,
}

/// Validation state for the custom domain / API key inputs.
#[derive(Debug, Clone)]
pub enum ValidationState {
    None,
    Valid,
    Invalid(String),
    Warning(String),
}

/// Text input state for the custom domain and API key screens.
#[derive(Debug, Clone)]
pub struct OnboardingInput {
    pub value: String,
    /// Caret position as a UTF-8 byte offset — the unit the composer's edit
    /// pipeline (`chat_input::apply`, `input_edit`) operates on.
    pub cursor: usize,
    pub validation: ValidationState,
}

impl Default for OnboardingInput {
    fn default() -> Self {
        Self {
            value: String::new(),
            cursor: 0,
            validation: ValidationState::None,
        }
    }
}

/// The full onboarding wizard state.
pub struct OnboardingState {
    pub open: bool,
    pub screen: Screen,
    pub context: OnboardingContext,
    /// The launch snapshot (Python `self._config`): what the server resolved
    /// before the wizard opened. The live `context` mutates with the user's
    /// choices; backing out of a browser sign-in restores from it, and the
    /// configured-domain reads and the tenant-resolution skip compare
    /// against it.
    pub config: OnboardingContext,
    pub theme_index: usize,
    pub auth_method_selected: usize,
    pub sign_in_target_selected: usize,
    pub custom_domain: OnboardingInput,
    /// The optional split-horizon browser-auth API base input.
    pub custom_domain_api: OnboardingInput,
    /// Which custom-domain input is focused: 0 the domain, 1 the API base.
    pub custom_domain_focus: usize,
    /// Which custom-domain input the feedback row describes; set on every
    /// edit and by Enter's validation, never by focus changes (Python's
    /// feedback is change-driven).
    pub custom_domain_feedback: usize,
    pub api_key_input: OnboardingInput,
    pub browser_sign_in: BrowserSignInState,
    pub welcome_char_index: usize,
    pub welcome_done: bool,
    pub welcome_timer: Option<Instant>,
    /// The app-server boot is still converging: the welcome screen says so
    /// instead of inviting an Enter the gate holds (the boot can take a
    /// first-run venv sync; Python's wizard has no server to wait for).
    pub boot_pending: bool,
    /// Armed by the first Enter on "Mistral AI" when a custom domain is
    /// configured; the second Enter applies the default (Python's override
    /// confirm).
    pub override_confirm_armed: bool,
    /// The configured domain the override warning names.
    pub override_confirm_domain: String,
    /// Whether the user selected a theme: Python persists `/theme` on
    /// every completed onboarding that selected one, selected value or not.
    pub theme_selected: bool,
    /// Top row of the theme preview's window (dragged by its scrollbar).
    pub preview_scroll: usize,
    /// Paint-time hit targets, rebuilt every frame: a list row rect per
    /// selectable theme row.
    pub theme_rows: Vec<(ratatui::layout::Rect, usize)>,
    /// Paint-time hit targets, rebuilt every frame: the card rect of every
    /// text input, for click-to-focus (Textual `Input` click behavior).
    pub input_rows: Vec<(ratatui::layout::Rect, InputCard)>,
}

/// Which wizard text input a painted card belongs to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputCard {
    Domain,
    DomainApi,
    ApiKey,
}

impl Default for OnboardingState {
    fn default() -> Self {
        // Cheap and I/O-free: tests must not read config.toml or the
        // environment. The effective context is seeded explicitly by
        // `seed` when the wizard opens, from the server's `setup/status`.
        let context = OnboardingContext::default();
        let theme_index = resolve_initial_theme_index(&context.theme);
        Self {
            open: false,
            screen: Screen::Welcome,
            config: context.clone(),
            context,
            theme_index,
            auth_method_selected: 0,
            sign_in_target_selected: 0,
            custom_domain: OnboardingInput::default(),
            custom_domain_api: OnboardingInput::default(),
            custom_domain_focus: 0,
            custom_domain_feedback: 0,
            api_key_input: OnboardingInput::default(),
            browser_sign_in: BrowserSignInState::default(),
            welcome_char_index: 0,
            welcome_done: false,
            welcome_timer: None,
            boot_pending: false,
            override_confirm_armed: false,
            override_confirm_domain: String::new(),
            theme_selected: false,
            preview_scroll: 0,
            theme_rows: Vec::new(),
            input_rows: Vec::new(),
        }
    }
}

/// Actions a screen key handler asks the flow to run. `None`-style key
/// presses (pure screen moves) return no action: the handler already set
/// `state.screen`.
#[derive(Debug, Clone)]
pub enum Action {
    Cancel,
    SelectTheme(usize),
    SubmitApiKey(String),
    StartBrowserSignIn,
    RetrySignIn,
    /// Switch to the API key screen, cancelling any in-flight sign-in flow
    /// first (Python `action_manual`'s `_cancel_current_attempt`).
    AbortSignIn,
    CopySignInUrl,
}

/// `--setup`'s welcome gate: while the round's background boot has not
/// landed, the Enter that would leave the welcome screen is held — the
/// seed from `setup/status` always precedes the first wizard choice.
pub fn holds_welcome_enter(boot_pending: bool, state: &OnboardingState, key: &KeyEvent) -> bool {
    boot_pending
        && state.screen == Screen::Welcome
        && state.welcome_done
        && key.code == KeyCode::Enter
}

impl OnboardingState {
    /// The custom-domain input the wizard key handling edits.
    pub fn focused_custom_domain_input(&mut self) -> &mut OnboardingInput {
        if self.custom_domain_focus == 0 {
            &mut self.custom_domain
        } else {
            &mut self.custom_domain_api
        }
    }
}

/// Seed the effective wizard context from the server's resolved view
/// (`setup/status`), refreshing the theme index. Called where the wizard
/// opens; `Default` stays I/O-free.
pub fn seed(state: &mut OnboardingState, status: &SetupStatus) {
    state.context = OnboardingContext::from_status(status);
    state.config = state.context.clone();
    state.theme_index = resolve_initial_theme_index(&state.context.theme);
}

/// Open the onboarding wizard (the fresh-install, `--setup`, and
/// missing-key rounds all open it the same way; the server resolved the
/// provider against the full effective config, project layer included).
pub fn open(state: &mut OnboardingState) {
    state.open = true;
    state.screen = Screen::Welcome;
    state.welcome_char_index = 0;
    state.welcome_done = false;
    state.welcome_timer = Some(Instant::now());
    // Replay captures settle mid-animation otherwise; show the full text.
    if crate::utils::is_replaying() {
        settle_welcome_typing(state);
    }
}

/// One 40ms step of the welcome typing animation (Python's `onboarding`
/// welcome screen). Replay settles instantly so captures stay stable.
pub fn advance_welcome_typing(state: &mut OnboardingState, replaying: bool) {
    if replaying {
        settle_welcome_typing(state);
    } else if state.welcome_char_index < WELCOME_TEXT.len() {
        state.welcome_char_index += 1;
    } else {
        state.welcome_done = true;
    }
}

/// Replay's settle: the full text, done — shared by the open and the
/// animation step so the two cannot drift.
fn settle_welcome_typing(state: &mut OnboardingState) {
    state.welcome_char_index = WELCOME_TEXT.len();
    state.welcome_done = true;
}

/// Close the onboarding wizard.
pub fn close(state: &mut OnboardingState) {
    state.open = false;
}

/// The initial theme index: the wizard context's theme, which is config.toml
/// overridden by VIBE_THEME, defaulting to auto (Python `resolve_theme_name`).
fn resolve_initial_theme_index(theme: &str) -> usize {
    let opts = crate::theme_picker::options();
    opts.iter().position(|t| *t == theme).unwrap_or(0)
}

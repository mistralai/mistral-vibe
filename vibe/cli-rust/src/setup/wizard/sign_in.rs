//! Browser sign-in state folding (Python `BrowserSignInService`'s reducer):
//! one sign-in event becomes wizard view state plus the actions it
//! resolves to (a completed sign-in submits its key).

use std::time::Instant;

use crate::setup::auth::sign_in_flow::{SignInEvent, SignInStatus};

use super::Action;

/// Fold one browser sign-in event into the wizard state, returning the actions
/// it resolves to (a completed sign-in submits its key).
pub fn sign_in_actions(state: &mut BrowserSignInState, event: SignInEvent) -> Vec<Action> {
    match event {
        SignInEvent::Started { sign_in_url } => {
            state.sign_in_url = Some(sign_in_url);
            // Replay pins the help visible so captures stay deterministic;
            // live, the copy help appears after the 4s help delay.
            state.show_url_help = crate::utils::is_replaying();
            state.reveal_sign_in_url = false;
            state.sign_in_started_at = Some(Instant::now());
            Vec::new()
        }
        SignInEvent::StatusChanged(status) => {
            match status {
                SignInStatus::OpeningBrowser => {
                    state.step = SignInStep::Open;
                    state.message = "Opening your browser...".into();
                }
                SignInStatus::Waiting => {
                    state.step = SignInStep::Confirm;
                    state.message = "Waiting for you to finish signing in...".into();
                }
                SignInStatus::Exchanging => {
                    state.step = SignInStep::Finish;
                    state.message = "Finishing setup...".into();
                }
                SignInStatus::Completed => {
                    // Python's FINISH step: "Sign-in complete" for the dwell.
                    state.step = SignInStep::Finish;
                    state.variant = SignInVariant::Success;
                    state.message = "Sign-in complete".into();
                }
            }
            Vec::new()
        }
        SignInEvent::Completed { api_key } => vec![Action::SubmitApiKey(api_key)],
        SignInEvent::Failed { message } => {
            state.variant = SignInVariant::Error;
            state.message = message;
            state.running = false;
            // Python `_show_error`: keep the copy help when a URL exists.
            state.show_url_help = state.sign_in_url.is_some();
            Vec::new()
        }
    }
}

/// Browser sign-in step state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignInStep {
    Open,
    Confirm,
    Finish,
}

/// Browser sign-in view state.
#[derive(Debug, Clone)]
pub struct BrowserSignInState {
    pub step: SignInStep,
    pub message: String,
    pub variant: SignInVariant,
    pub running: bool,
    pub sign_in_url: Option<String>,
    pub show_url_help: bool,
    /// The copy key revealed the raw URL (Python `reveal_sign_in_url`).
    pub reveal_sign_in_url: bool,
    /// When the sign-in URL landed; the copy help appears 4s later unless the
    /// copy key set `show_url_help` already (Python `SIGN_IN_URL_HELP_DELAY`).
    pub sign_in_started_at: Option<Instant>,
}

impl BrowserSignInState {
    /// A spawned flow that has not reached its waiting step nor failed yet.
    pub fn is_starting(&self) -> bool {
        self.running && self.variant == SignInVariant::Pending && self.step == SignInStep::Open
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignInVariant {
    Pending,
    Error,
    Success,
}

impl Default for BrowserSignInState {
    fn default() -> Self {
        Self {
            step: SignInStep::Open,
            message: "Getting things ready...".into(),
            variant: SignInVariant::Pending,
            running: false,
            sign_in_url: None,
            show_url_help: false,
            reveal_sign_in_url: false,
            sign_in_started_at: None,
        }
    }
}

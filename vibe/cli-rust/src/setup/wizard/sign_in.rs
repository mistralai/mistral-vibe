//! Browser sign-in state folding: one sign-in event becomes wizard view
//! state plus the actions it resolves to (a completed sign-in submits its
//! key).

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
                    // The finish step shows "Sign-in complete" for the dwell.
                    state.step = SignInStep::Finish;
                    state.variant = SignInVariant::Success;
                    state.message = "Sign-in complete".into();
                }
            }
            Vec::new()
        }
        SignInEvent::Completed { api_key } => vec![Action::SubmitApiKey(api_key)],
        SignInEvent::OidcCompleted => vec![Action::CompleteOidc],
        SignInEvent::Failed { message } => {
            state.variant = SignInVariant::Error;
            state.message = message;
            state.running = false;
            // Keep the copy help when a URL exists.
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
    /// The copy key revealed the raw URL.
    pub reveal_sign_in_url: bool,
    /// When the sign-in URL landed; the copy help appears 4s later unless the
    /// copy key set `show_url_help` already.
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

/// The step card titles and their detail one-liners, pending and done.
pub const STEP_TITLES: [&str; 3] = ["Open browser", "Complete sign-in", "Finished setup"];
pub const STEP_PENDING_DETAILS: [&str; 3] = [
    "Your browser should open automatically",
    "Waiting for authentication...",
    "Vibe will start automatically",
];
pub const STEP_DONE_DETAILS: [&str; 3] =
    ["Browser opened", "Sign-in confirmed.", "Setup complete."];
/// The waiting-for-authentication message, painted as a gradient.
pub const WAITING_FOR_AUTHENTICATION: &str = "Waiting for authentication...";

/// The step card at `index`'s detail lines: the fixed one-liners everywhere
/// except the active card, whose status message wraps to the card's content
/// width (the border and padding inset the text by 4 on each side) and is
/// clamped to `max_lines` lines so the cards fit a short terminal.
pub fn step_detail_lines(
    state: &BrowserSignInState,
    step_idx: usize,
    index: usize,
    card_width: u16,
    max_lines: usize,
) -> Vec<String> {
    if index < step_idx {
        vec![STEP_DONE_DETAILS[index].to_owned()]
    } else if index > step_idx {
        vec![STEP_PENDING_DETAILS[index].to_owned()]
    } else if state.variant == SignInVariant::Pending && state.step == SignInStep::Confirm {
        vec![WAITING_FOR_AUTHENTICATION.to_owned()]
    } else {
        let mut lines = crate::utils::text::wrap_hard(
            &state.message,
            card_width.saturating_sub(8).max(1) as usize,
        );
        if lines.len() > max_lines {
            lines.truncate(max_lines);
            let last = lines.last_mut().expect("max_lines >= 1");
            *last = format!("{}…", last.strip_suffix('…').unwrap_or(last));
        }
        lines
    }
}

/// The wrapped-line allowance for the active card inside a `budget`-row
/// steps stack: eleven rows are the borders and the two fixed cards.
pub fn max_detail_lines(budget: u16) -> usize {
    budget.saturating_sub(11).max(1) as usize
}

/// The three step cards' total height within a `budget`-row stack, so the
/// walk never drops the whole stack for not fitting: each card is its
/// border, its title, and its detail lines. `u16::MAX` leaves the message
/// unclamped.
pub fn steps_height(
    state: &BrowserSignInState,
    step_idx: usize,
    card_width: u16,
    budget: u16,
) -> u16 {
    let max_lines = max_detail_lines(budget);
    (0..3)
        .map(|index| {
            step_detail_lines(state, step_idx, index, card_width, max_lines).len() as u16 + 3
        })
        .sum()
}

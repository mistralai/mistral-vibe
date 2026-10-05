//! The wizard's `setup/submit-choices` payload (Python
//! `persist_credentials` plus the post-exit `/theme` write): the provider
//! and base-URL fields on every completion — the server judges drift — and
//! the theme whenever one was selected. Sent before the session exists —
//! the client never writes config.toml itself (ADR 0016).

use super::OnboardingState;
use crate::setup::auth::rpc::SubmitChoices;

/// The wizard's completion payload: the full choices, every completed wizard
/// (absent fields stay the server's to decide, per its own drift check).
pub fn choices(state: &OnboardingState) -> SubmitChoices {
    SubmitChoices {
        provider: Some(state.context.provider.clone()),
        console_base_url: Some(state.context.console_base_url.clone()),
        vibe_base_url: Some(state.context.vibe_base_url.clone()),
        theme: state
            .theme_selected
            .then(|| {
                crate::theme_picker::options()
                    .get(state.theme_index)
                    .copied()
                    .map(str::to_owned)
            })
            .flatten(),
    }
}

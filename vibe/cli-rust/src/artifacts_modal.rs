//! The artifacts modal: every PR/MR artifact of the session as a link list,
//! opened from the bottom bar when several artifacts share the one link slot.

use crate::app::App;
use crate::external_url;
use crate::server::PublicSessionArtifact;

/// Open the modal highlighted on the latest artifact (the list is kept
/// latest-first).
pub fn open(app: &mut App) {
    app.artifacts_modal.list = crate::option_list::OptionList::new(0);
    app.artifacts_modal.open = true;
}

/// Close the modal without opening anything (Esc).
pub fn close(app: &mut App) {
    app.artifacts_modal.open = false;
}

/// Move the highlight up/down by one, wrapping at the ends.
pub fn navigate(app: &mut App, down: bool) {
    let count = option_count(app);
    app.artifacts_modal.list.step(down, count, true, |_| true);
}

/// The highlighted artifact, if any.
pub fn selected(app: &App) -> Option<&PublicSessionArtifact> {
    app.session
        .artifacts
        .get(app.artifacts_modal.list.selected())
}

/// Open the highlighted artifact's link and close the modal.
pub fn select(app: &mut App) {
    if let Some(artifact) = selected(app) {
        external_url::open(&artifact.external_url);
    }
    close(app);
}

/// Row count: one per artifact.
pub fn option_count(app: &App) -> usize {
    app.session.artifacts.len()
}

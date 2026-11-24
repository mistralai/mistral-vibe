//! Leaving the page the moment a session is picked: the conversation view
//! shows a loader while the server opens it, and comes back if that fails.

use crate::app::App;
use crate::transcript::Snapshot;
use crate::ui::loading::OPENING_SESSION_STATUS;

/// A switch in flight: the session it opens (empty for a new one) and the
/// transcript, with its scroll, to put back when it fails.
pub struct Switching {
    pub id: String,
    previous: Snapshot,
    scroll: (u16, u16),
}

/// Whether the conversation view is waiting on a switch: it takes no prompt meanwhile.
pub fn switching(app: &App) -> bool {
    app.sessions_page.switching.is_some()
}

/// Close the page on an empty conversation with a loader; the request is in flight.
pub(super) fn start(app: &mut App, id: String) {
    let previous = app.view.transcript.snapshot();
    let scroll = (app.view.scroll, app.view.scroll_target);
    // The preview read its history: it paints at once, until the opened session's lands.
    match app.sessions_page.preview.snapshot(&id) {
        Some(cached) => {
            app.view.transcript.restore(cached);
            app.view.scroll = 0;
            app.view.scroll_target = 0;
        }
        None => app.view.transcript.clear(),
    }
    app.view.transcript_cache.invalidate_layouts();
    if app.begin_command_loading() {
        app.view.loading.set_label(OPENING_SESSION_STATUS);
    }
    app.sessions_page.switching = Some(Switching {
        id,
        previous,
        scroll,
    });
    app.sessions_page.open = false;
    // The session's own resume reads its history: a settling preview read would fetch it twice.
    super::preview::cancel(app);
}

/// The session opened: the loader goes, the adopted history takes the view.
pub(super) fn landed(app: &mut App) {
    if app.sessions_page.switching.take().is_some() {
        app.view.command_loading = false;
    }
}

/// The switch failed: the previous conversation comes back under the page,
/// which reopens to say why.
pub(super) fn failed(app: &mut App) {
    if abandon(app) {
        app.sessions_page.open = true;
    }
}

/// Put the previous conversation back; false when no switch was in flight.
pub(super) fn abandon(app: &mut App) -> bool {
    let Some(switching) = app.sessions_page.switching.take() else {
        return false;
    };
    app.view.transcript.restore(switching.previous);
    (app.view.scroll, app.view.scroll_target) = switching.scroll;
    app.view.transcript_cache.invalidate_layouts();
    app.view.command_loading = false;
    true
}

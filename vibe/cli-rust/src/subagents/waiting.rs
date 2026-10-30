//! Waiting children: hidden from the list outside the running batch, `a` shows them.

use std::collections::HashSet;

use crate::app::App;
use crate::server::{PublicChildSession, SessionStatus};

/// Drop waiting children outside the batch and the view unless `show_waiting`; also returns their count.
pub(super) fn hide(
    rows: Vec<PublicChildSession>,
    batch: &HashSet<String>,
    selected_session_id: Option<&str>,
    show_waiting: bool,
) -> (Vec<PublicChildSession>, usize) {
    let hideable = |session: &PublicChildSession| {
        session.status == SessionStatus::Idle
            && !batch.contains(&session.id)
            && selected_session_id != Some(session.id.as_str())
    };
    let waiting = rows.iter().filter(|session| hideable(session)).count();
    if show_waiting {
        return (rows, waiting);
    }
    let rows = rows.into_iter().filter(|session| !hideable(session));
    (rows.collect(), waiting)
}

/// Show or hide the waiting children; a no-op when none is waiting.
pub(super) fn toggle(app: &mut App) {
    if app.subagents.list.waiting_count == 0 {
        return;
    }
    app.subagents.list.show_waiting = !app.subagents.list.show_waiting;
    super::list::update_sessions(app);
}

//! Timed wake-ups: the typing pauses (a user who never typed has already paused), and the next-loop text.

use crate::clock::UiInstant;

use crate::app::App;

pub(super) fn typing_pause_deadline(app: &App) -> Option<UiInstant> {
    Some(crate::question_app::typing_pause_deadline(app).unwrap_or_else(crate::clock::now))
}

pub(super) fn approval_pause_deadline(app: &App) -> Option<UiInstant> {
    Some(crate::approval::typing_pause_deadline(app).unwrap_or_else(crate::clock::now))
}

/// When a shown next-loop delay changes (the rail's loops chip or a manager row), so the screen redraws then.
pub(super) fn next_loop_deadline(app: &App) -> Option<UiInstant> {
    let now = crate::clock::unix_now_seconds();
    let wait = crate::loops::schedule::next_redraw(&app.session.loops, now);
    wait.is_finite()
        .then(|| crate::clock::now() + std::time::Duration::from_secs_f64(wait))
}

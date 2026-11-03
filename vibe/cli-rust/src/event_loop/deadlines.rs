//! The typing-pause wake-ups: a user who never typed has already paused.

use crate::clock::UiInstant;

use crate::app::App;

pub(super) fn typing_pause_deadline(app: &App) -> Option<UiInstant> {
    Some(crate::question_app::typing_pause_deadline(app).unwrap_or_else(crate::clock::now))
}

pub(super) fn approval_pause_deadline(app: &App) -> Option<UiInstant> {
    Some(crate::approval::typing_pause_deadline(app).unwrap_or_else(crate::clock::now))
}

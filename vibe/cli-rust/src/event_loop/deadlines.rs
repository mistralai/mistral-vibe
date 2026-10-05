//! The event loop's select deadlines: one per deferred wake-up the loop
//! sleeps on, each returning a far-future bound when nothing is armed.

use std::time::Duration;

use crate::app::App;

pub(super) fn preview_deadline(app: &App) -> tokio::time::Instant {
    app.theme_picker
        .preview_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

pub(super) fn spinner_deadline(app: &App) -> tokio::time::Instant {
    app.agents
        .spinner_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

pub(super) fn typing_pause_deadline(app: &App) -> tokio::time::Instant {
    crate::question_app::typing_pause_deadline(app)
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(tokio::time::Instant::now)
}

pub(super) fn approval_pause_deadline(app: &App) -> tokio::time::Instant {
    crate::approval::typing_pause_deadline(app)
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(tokio::time::Instant::now)
}

pub(super) fn feedback_deadline(app: &App) -> tokio::time::Instant {
    app.feedback
        .hide_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

pub(super) fn subagent_refresh_deadline(app: &App) -> tokio::time::Instant {
    app.subagents
        .refresh_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

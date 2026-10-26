//! Loading-line key hints, gated by the state their keys dispatch on.

use ratatui::text::Span;

use crate::app::App;
use crate::hints::{action, key, Hint};
use crate::message_queue;
use crate::ui::hint_line;

const CANCEL_LAST_QUEUED: Hint = (key::CTRL_C, action::CANCEL_LAST_QUEUED);

/// Startup has no turn to interrupt; only queue cancellation applies.
pub(super) fn starting(app: &App) -> Vec<Hint> {
    if !message_queue::has_removable(app)
        || !crate::input::composer_reachable(app)
        || !app.chat_input.full_text().is_empty()
    {
        return Vec::new();
    }
    vec![CANCEL_LAST_QUEUED]
}

/// A running turn or shell command; queue actions need a reachable, empty input.
pub(super) fn running(app: &App) -> Vec<Hint> {
    // A picker owns Esc and Ctrl+C, so neither interrupts nor cancels behind it.
    if app.picker_focused() {
        return Vec::new();
    }
    // Ctrl+C cancels a queued prompt before interrupting.
    if !message_queue::has_removable(app) {
        return vec![(key::ESC_CTRL_C, action::INTERRUPT)];
    }
    let mut hints = vec![(key::ESC, action::INTERRUPT)];
    if !crate::input::composer_reachable(app) {
        return hints;
    }
    let text = app.chat_input.full_text();
    if text.trim().is_empty() && message_queue::can_steer(app) {
        hints.push((key::ENTER, action::STEER));
    }
    if text.is_empty() {
        hints.push(CANCEL_LAST_QUEUED);
    }
    hints
}

/// Push `(prefix key to action · …)`; only `(prefix)` without hints, nothing without either.
pub(super) fn push(spans: &mut Vec<Span<'_>>, prefix: String, hints: &[Hint]) {
    let muted = hint_line::action_style();
    if hints.is_empty() {
        let prefix = prefix.trim_end();
        if !prefix.is_empty() {
            spans.push(Span::styled(format!("({prefix})"), muted));
        }
        return;
    }
    let key = hint_line::key_style();
    spans.push(Span::styled(format!("({prefix}"), muted));
    for (index, (keys, action)) in hints.iter().enumerate() {
        if index > 0 {
            spans.push(Span::styled(" · ", muted));
        }
        spans.push(Span::styled(*keys, key));
        spans.push(Span::styled(format!(" to {action}"), muted));
    }
    spans.push(Span::styled(")", muted));
}

#[cfg(test)]
mod tests;

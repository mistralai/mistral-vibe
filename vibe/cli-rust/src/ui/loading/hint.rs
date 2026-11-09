//! Loading-line key hints, gated by the state their keys dispatch on.

use crossterm::event::KeyCode;
use ratatui::text::Span;

use crate::app::App;
use crate::focus::Focus;
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
    // A picker owns Esc and Ctrl+C; in a child view Ctrl+C only warns, so neither interrupts.
    if app.picker_focused() || app.subagents.viewed_subagent_id.is_some() {
        return Vec::new();
    }
    // The focused subagent list takes Esc back to the input; Ctrl+C first clears a typed prompt.
    if app.focus() == Focus::SubagentList {
        if !app.chat_input.full_text().is_empty() {
            return Vec::new();
        }
        return match message_queue::has_removable(app) {
            true => vec![CANCEL_LAST_QUEUED],
            false => vec![(key::CTRL_C, action::INTERRUPT)],
        };
    }
    // Vim Insert mode takes Esc to Normal mode, so only Ctrl+C interrupts.
    let esc_interrupts =
        !crate::vim::inserting(app) || !crate::vim::owns(app, &KeyCode::Esc.into());
    // Ctrl+C cancels a queued prompt before interrupting.
    if !message_queue::has_removable(app) {
        let keys = if esc_interrupts {
            key::ESC_CTRL_C
        } else {
            key::CTRL_C
        };
        return vec![(keys, action::INTERRUPT)];
    }
    let mut hints = match esc_interrupts {
        true => vec![(key::ESC, action::INTERRUPT)],
        false => Vec::new(),
    };
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
    spans.push(Span::styled(format!("({prefix}"), muted));
    spans.extend(hint_line::inline_spans(hints));
    spans.push(Span::styled(")", muted));
}

#[cfg(test)]
mod tests;

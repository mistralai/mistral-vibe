//! Loading-line key hints, gated by the state their keys dispatch on.

use crossterm::event::KeyCode;
use ratatui::text::Span;

use crate::app::{App, Status};
use crate::focus::Focus;
use crate::hints::{action, key, Hint};
use crate::message_queue;
use crate::ui::hint_line;

const CANCEL_LAST_QUEUED: Hint = (key::CTRL_C, action::CANCEL_LAST_QUEUED);

/// The queue's own keys, on its `» Queued` header so the loading line keeps room for the rail.
pub fn queue(app: &App) -> &'static [Hint] {
    const STEER: Hint = (key::ENTER, action::STEER);
    let background = app.view.command_loading && app.session.shell_operation_id.is_none();
    let hidden = app.picker_focused()
        || app.subagents.viewed_subagent_id.is_some()
        || crate::rail::window_open(app)
        || app.queue.selected.is_some();
    if background || hidden || !message_queue::has_removable(app) {
        return &[];
    }
    let text = app.chat_input.full_text();
    // The focused rail takes Enter, so only Ctrl+C reaches the queue there.
    let rail = app.focus() == Focus::Rail;
    let composer = crate::input::composer_reachable(app);
    let generating = matches!(app.session.status, Status::Generating { .. });
    let steer = composer && generating && text.trim().is_empty() && message_queue::can_steer(app);
    let cancel = (composer || rail) && text.is_empty();
    match (steer, cancel) {
        (true, true) => &[STEER, CANCEL_LAST_QUEUED],
        (true, false) => &[STEER],
        (false, true) => &[CANCEL_LAST_QUEUED],
        (false, false) => &[],
    }
}

/// A running turn or shell command's interrupt; the queue's keys sit on its header (see [`queue`]).
pub(super) fn running(app: &App) -> Vec<Hint> {
    // A picker owns Esc and Ctrl+C; in a child view Ctrl+C only warns, so neither interrupts.
    if app.picker_focused() || app.subagents.viewed_subagent_id.is_some() {
        return Vec::new();
    }
    // The focused rail takes Esc back to the input; Ctrl+C first closes an open window, else clears a typed prompt.
    if matches!(app.focus(), Focus::SubagentList | Focus::Rail) {
        if !app.chat_input.full_text().is_empty() || crate::rail::window_open(app) {
            return Vec::new();
        }
        return match message_queue::has_removable(app) {
            true => Vec::new(),
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
    match esc_interrupts {
        true => vec![(key::ESC, action::INTERRUPT)],
        false => Vec::new(),
    }
}

/// Push `(prefix key to action · …)`; only `(prefix)` without hints, nothing without either.
pub(super) fn push(spans: &mut Vec<Span<'static>>, prefix: String, hints: &[Hint]) {
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

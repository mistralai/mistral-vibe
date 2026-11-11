//! Applying vim edits and caret moves to the composer.

use super::motion::{last_char, left, next_word_start, word_end};
use super::Mode;
use crate::app::{App, ChatInput};
use crate::chat_input::clamp_offset;
use crate::edit_history::Snapshot;

/// Delete `from..to` as one undoable edit.
pub(super) fn delete(app: &mut App, (from, to): (usize, usize)) {
    let before = Snapshot::capture(&app.chat_input);
    crate::input::reset_history_state(app);
    crate::long_paste::dismiss(app);
    app.chat_input.scroll = None;
    app.chat_input
        .edit_atomically(None, |input, cursor, anchor| {
            input.replace_range(from..to, "");
            (*cursor, *anchor) = (from, None);
        });
    app.chat_input
        .record_edit(before, false, crate::clock::now());
    crate::completion_manager::input_changed(app);
}

/// Put the caret at byte offset `to`, dropping any selection.
pub(super) fn move_caret(app: &mut App, to: usize) {
    app.chat_input.scroll = None;
    app.chat_input.anchor = None;
    let from = app.chat_input.cursor;
    app.chat_input.cursor = to;
    app.chat_input.snap_cursor(from);
    crate::input::mark_cursor_moved(app);
    crate::completion_manager::refresh(app);
}

/// Where `w` or `e` lands when its target `to` is inside a mention, which counts as one word.
pub(super) fn past_mention(chat: &ChatInput, motion: char, to: usize) -> usize {
    let Some((_, end)) = chat.mentions.around(to) else {
        return to;
    };
    let input = &chat.input;
    match motion {
        'w' if input[end..].starts_with(|c: char| !c.is_whitespace()) => end,
        'w' => next_word_start(input, end),
        _ => word_end(input, left(input, end)),
    }
}

/// The caret as Normal mode shows it: on a character, never past its line's end.
pub fn caret(app: &App) -> usize {
    let chat = &app.chat_input;
    let cursor = clamp_offset(&chat.input, chat.cursor);
    let normal = app.vim.enabled
        && app.vim.mode == Mode::Normal
        && chat.anchor.is_none()
        && crate::input::composer_owns_key(app);
    match normal {
        true => cursor.min(last_char(&chat.input, cursor)),
        false => cursor,
    }
}

/// Move a Normal-mode caret left past a line end back onto the last character.
pub(super) fn settle(app: &mut App) {
    let last = caret(app);
    let chat = &mut app.chat_input;
    if last == chat.cursor {
        return;
    }
    // A recalled entry stays unmoved, so the next `k` recalls the entry before it.
    if chat.cursor_pos_after_load == Some(chat.cursor) {
        chat.cursor_pos_after_load = Some(last);
    }
    move_caret(app, last);
}

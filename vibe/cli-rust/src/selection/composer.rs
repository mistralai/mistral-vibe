//! Composer surface: map screen cells to chat-input offsets and snap selections.

use crate::app::App;
use crate::chat_input;
use crate::selection::Granularity;
use crate::utils::input_edit;

/// True when `at` is anywhere inside the chat input box (Python `ChatInputContainer`).
pub fn in_input(app: &App, at: (u16, u16)) -> bool {
    let area = app.view.input_area;
    at.0 >= area.x && at.0 < area.right() && at.1 > area.y && at.1 < area.bottom()
}

/// Byte offset of the cell at `at`, or `None` outside the editable body.
pub fn offset_at(app: &App, at: (u16, u16)) -> Option<usize> {
    let area = app.view.input_area;
    let top = area.y.saturating_add(1);
    let bottom = area.bottom().saturating_sub(1);
    if at.0 < area.x.saturating_add(2) || at.0 >= area.right().saturating_sub(1) {
        return None;
    }
    if at.1 < top || at.1 >= bottom {
        return None;
    }
    Some(clamped_offset(app, at))
}

/// Byte offset of the cell at `at`, clamped into the document (used while dragging).
fn clamped_offset(app: &App, at: (u16, u16)) -> usize {
    let area = app.view.input_area;
    let top = area.y.saturating_add(1);
    let text = &app.chat_input.input;
    let marker = app.chat_input.mode.marker();
    let viewport = area.height.saturating_sub(2) as usize;
    let scroll = crate::ui::chat_input::scroll(app, area.width, viewport) as usize;
    let row = at.1.saturating_sub(top) as usize + scroll;
    let column = at.0.saturating_sub(area.x.saturating_add(2)) as usize;
    let layout = crate::ui::composer_layout::ComposerLayout::new(text, marker, 0, area.width);
    layout.offset_at(row, column).min(text.len())
}

/// Press at `offset`: park the caret there, then widen to the click granularity
/// (Python `ChatTextArea._on_mouse_down`).
pub(super) fn press(app: &mut App, offset: usize) {
    app.chat_input.scroll = None;
    app.selection.composer_anchor = Some(offset);
    let (anchor, cursor) = match app.selection.granularity {
        Granularity::Char => (offset, offset),
        Granularity::Word => word_bounds(app, offset).unwrap_or((offset, offset)),
        Granularity::Paragraph => input_edit::line_bounds(&app.chat_input.input, offset),
    };
    app.chat_input.anchor = Some(anchor);
    app.chat_input.cursor = cursor;
    crate::completion_manager::refresh(app);
}

/// Extend to `at`, snapping both ends outwards for a word or paragraph drag
/// (Python `_snap_word_drag` / `_snap_paragraph_drag`).
pub(super) fn drag(app: &mut App, at: (u16, u16)) {
    let head = clamped_offset(app, at);
    let Some(anchor) = app.selection.composer_anchor else {
        app.chat_input.cursor = head;
        return;
    };
    if app.selection.granularity == Granularity::Char {
        app.chat_input.anchor = Some(anchor);
        app.chat_input.cursor = head;
        return;
    }
    let (lo, hi) = if anchor <= head {
        (anchor, head)
    } else {
        (head, anchor)
    };
    let (start, end) = match app.selection.granularity {
        Granularity::Paragraph => (
            input_edit::line_bounds(&app.chat_input.input, lo).0,
            input_edit::line_bounds(&app.chat_input.input, hi).1,
        ),
        _ => (
            word_bounds(app, lo).map_or(lo, |(start, _)| start),
            word_bounds(app, hi).map_or(hi, |(_, end)| end),
        ),
    };
    app.chat_input.anchor = Some(start);
    app.chat_input.cursor = end;
}

/// Copy the completed composer selection, or drop an empty one.
pub(super) fn release(app: &mut App) -> Option<String> {
    app.selection.composer_anchor = None;
    let text = chat_input::selected_text(
        &app.chat_input.input,
        app.chat_input.cursor,
        app.chat_input.anchor,
    );
    if text.is_none() {
        app.chat_input.anchor = None;
    }
    text
}

fn word_bounds(app: &App, offset: usize) -> Option<(usize, usize)> {
    input_edit::word_bounds(&app.chat_input.input, offset)
}

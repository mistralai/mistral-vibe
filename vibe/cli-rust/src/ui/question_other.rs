//! The question box's free-text field: the answer wraps under its option prefix
//! and scrolls inside at most `OTHER_MAX_ROWS` rows.

use ratatui::style::Style;
use unicode_width::UnicodeWidthStr;

use super::composer_layout::{self, ComposerLayout};
use super::question_layout::Row;
use super::question_rows::option_prefix;
use super::tab_cells::cells;
use super::{list_cursor, theme};
use crate::app::App;
use crate::question_app::{current_question, is_other_selected, other_text};
use crate::selection::fold;

pub const OTHER_MAX_ROWS: usize = 5;

/// The current answer wrapped to `text_width`, its last cell kept for the caret.
pub(crate) fn field_layout(app: &App, text_width: u16) -> ComposerLayout<'_> {
    let text = other_text(app, app.question_app.current_question_idx);
    ComposerLayout::new(text, "", app.question_app.other_cursor, text_width)
}

/// Cells left for the answer once the option prefix is drawn.
pub(super) fn text_width(app: &App, other_idx: usize, content_width: usize) -> u16 {
    let prefix = option_prefix(other_idx, current_question(app).multi_select, false);
    let width = content_width.saturating_sub(prefix.chars().count());
    u16::try_from(width).unwrap_or(u16::MAX)
}

/// Keep the field scroll in range, following the caret only while focused.
pub(super) fn follow_caret(app: &mut App, text_width: u16) {
    let layout = field_layout(app, text_width);
    let top = app.question_app.other_scroll;
    let top = match is_other_selected(app) {
        true => layout.viewport_top(top, OTHER_MAX_ROWS),
        false => top.min(usize::from(layout.height()).saturating_sub(OTHER_MAX_ROWS)),
    };
    app.question_app.other_scroll = top;
}

/// The field rows, each flagged when it ends with a scroll arrow and paired with
/// its fold gap when it soft-wraps the row above: the prefix, then the answer with
/// its caret, or the placeholder. Focused, every row is a cursor bar like a
/// focused option's.
pub(super) fn other_rows(
    app: &App,
    other_idx: usize,
    content_width: usize,
) -> Vec<(Row, bool, Option<u16>)> {
    let focused = is_other_selected(app);
    let selected = app
        .question_app
        .multi_selections
        .get(&app.question_app.current_question_idx)
        .is_some_and(|selections| selections.contains(&other_idx));
    let prefix = option_prefix(other_idx, current_question(app).multi_select, selected);
    let (body, muted) = match focused {
        true => list_cursor::styles(true),
        false => (theme::text(theme::foreground()), theme::muted_style()),
    };
    let caret = focused && app.view.cursor_on;
    let text = other_text(app, app.question_app.current_question_idx);
    if text.is_empty() {
        return vec![(placeholder_row(prefix, body, muted, caret), false, None)];
    }

    let text_width = text_width(app, other_idx, content_width);
    let layout = field_layout(app, text_width);
    let top = app.question_app.other_scroll;
    let hidden_below = usize::from(layout.height()) > top + OTHER_MAX_ROWS;
    let indent = " ".repeat(prefix.chars().count());
    let rows: Vec<_> = layout.rows().collect();
    let gaps = fold::gaps(text, &rows.iter().map(|row| row.text).collect::<Vec<_>>());
    let visible: Vec<_> = rows
        .into_iter()
        .zip(gaps)
        .skip(top)
        .take(OTHER_MAX_ROWS)
        .collect();
    let last = visible.len().saturating_sub(1);
    visible
        .into_iter()
        .enumerate()
        .map(|(index, (visual, gap))| {
            let lead = match index {
                0 => prefix.clone(),
                _ => indent.clone(),
            };
            let mut row = vec![(lead, body)];
            push_text(
                &mut row,
                visual,
                caret.then(|| layout.caret_in(visual)).flatten(),
                body,
            );
            let arrow = match index {
                0 if top > 0 => Some("↑"),
                _ if index == last && hidden_below => Some("↓"),
                _ => None,
            };
            let arrow =
                arrow.is_some_and(|arrow| push_indicator(&mut row, arrow, muted, content_width));
            (row, arrow, gap)
        })
        .collect()
}

/// Empty means the placeholder, whether from the static or the focused `Input`.
fn placeholder_row(prefix: String, body: Style, muted: Style, caret: bool) -> Row {
    let mut row = vec![(prefix, body)];
    if caret {
        row.push(("T".into(), list_cursor::caret()));
        row.push(("ype your answer...".into(), muted));
    } else {
        row.push(("Type your answer...".into(), muted));
    }
    row
}

fn push_text(row: &mut Row, visual: composer_layout::Row<'_>, caret: Option<usize>, style: Style) {
    let Some(at) = caret else {
        row.push((visual.shown(..), style));
        return;
    };
    let (lo, hi) = cells(visual.text, visual.column)
        .map(|cell| (cell.byte, cell.byte + cell.symbol.len()))
        .find(|&(_, hi)| hi > at)
        .unwrap_or((at, at));
    let under = match lo == hi {
        true => " ".to_owned(),
        false => visual.shown(lo..hi),
    };
    row.push((visual.shown(..lo), style));
    row.push((under, list_cursor::caret()));
    row.push((visual.shown(hi..), style));
}

/// Right-align a muted scroll arrow in the field's last column, when it is free.
fn push_indicator(row: &mut Row, arrow: &str, muted: Style, content_width: usize) -> bool {
    let used: usize = row.iter().map(|(text, _)| text.width()).sum();
    let column = content_width.saturating_sub(1);
    if used > column {
        return false;
    }
    row.push((" ".repeat(column - used), muted));
    row.push((arrow.into(), muted));
    true
}

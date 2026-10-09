//! The search row shared by every searchable list: `/`, then a horizontally
//! bounded query with a software caret.

use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use crate::chat_input;
use crate::search_field::Search;
use crate::ui::theme;

/// Columns from the `/` to the field: the slash and two spaces.
const FIELD_INDENT: u16 = 3;

/// The search row of a searchable list: a dim `/`, then the field. Returns the
/// field's area so the caller can route clicks to it.
pub(crate) fn draw_row(
    f: &mut Frame,
    row: Rect,
    search: &Search,
    placeholder: &str,
    cursor_on: bool,
    bg: Color,
) -> Rect {
    let dim = theme::dim(theme::text_muted()).bg(bg);
    f.buffer_mut().set_string(row.x, row.y, "/", dim);
    let input = Rect::new(
        row.x + FIELD_INDENT,
        row.y,
        row.width.saturating_sub(FIELD_INDENT),
        1,
    );
    draw_field(f, input, search, placeholder, cursor_on, bg);
    input
}

/// A one-line query field: placeholder or query, selection, and a caret kept in view.
fn draw_field(
    f: &mut Frame,
    input: Rect,
    search: &Search,
    placeholder: &str,
    cursor_on: bool,
    bg: Color,
) {
    if input.width == 0 {
        return;
    }
    let dim = theme::dim(theme::text_muted()).bg(bg);
    let (text, style) = if search.query.is_empty() {
        (placeholder, dim)
    } else {
        (search.query.as_str(), theme::text(theme::foreground()))
    };
    let caret = (search.focused && cursor_on).then_some(search.cursor);
    let selection = search
        .focused
        .then(|| chat_input::selection_range(&search.query, search.cursor, search.anchor))
        .flatten();
    let offset = if search.focused {
        search.query[..search.cursor]
            .width()
            .saturating_sub(input.width as usize - 1)
    } else {
        0
    };
    let mut column = 0;
    for (index, ch) in text
        .char_indices()
        .chain(std::iter::once((text.len(), ' ')))
    {
        let value = ch.to_string();
        let width = value.width();
        let selected = selection.is_some_and(|(start, end)| index >= start && index < end);
        if column >= offset && column + width <= offset + input.width as usize {
            let style = if caret == Some(index) || selected {
                caret_style(style)
            } else {
                style
            };
            f.buffer_mut()
                .set_string(input.x + (column - offset) as u16, input.y, value, style);
        }
        column += width;
        if column >= offset + input.width as usize {
            break;
        }
    }
}

fn caret_style(style: Style) -> Style {
    theme::block_caret(style)
}

//! Search field with a horizontally bounded query and software caret.

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use crate::app::App;
use crate::chat_input;
use crate::ui::theme;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let dim = theme::dim(theme::text_muted()).bg(theme::background());
    let input = Rect::new(area.x + 6, area.y + 2, area.width.saturating_sub(9), 1);
    app.mcp.search.area = input;
    crate::mouse::register_region(app, input, crate::mouse::MouseTarget::Mcp);
    f.buffer_mut().set_string(area.x + 3, input.y, "/", dim);
    if input.width == 0 {
        return;
    }
    let search = &app.mcp.search;
    let (text, style) = if search.query.is_empty() {
        ("Search servers and connectors (/ or Left to focus)", dim)
    } else {
        (search.query.as_str(), theme::text(theme::foreground()))
    };
    let caret = (search.focused && app.view.cursor_on).then_some(search.cursor);
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
    style
        .fg(theme::block_cursor_fg())
        .bg(theme::block_cursor_bg())
}

//! The rename modal over the Sessions page: the title wrapped to the box, with its caret, then its keys.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::super::goal_panel::{body, draw_hints, frame, heading};
use super::super::tab_cells::cells;
use super::super::theme;
use crate::app::App;
use crate::hints::{self, action, key, Hint};

const HINTS: &[Hint] = &[(key::ENTER, action::SAVE), hints::CANCEL];
/// Title rows shown before the field scrolls.
const MAX_ROWS: usize = 6;
const WIDTH: u16 = 72;

/// Paint the modal centered on `page`, when a rename is open.
pub(super) fn draw(app: &mut App, f: &mut Frame, page: Rect) {
    let cursor_on = app.view.cursor_on;
    let Some(rename) = app.sessions_page.rename.as_mut() else {
        return;
    };
    let width = WIDTH.min(page.width.saturating_sub(4));
    // Border and padding take 3 columns a side; the caret may sit one past the text.
    rename.area.width = width.saturating_sub(6).max(1);
    let rows = (rename.layout().height() as usize).clamp(1, MAX_ROWS) as u16;
    let height = rows + 6;
    let area = Rect::new(
        page.x + page.width.saturating_sub(width) / 2,
        page.y + page.height.saturating_sub(height) / 2,
        width,
        height.min(page.height),
    );
    let Some(content) = frame(f, area, vec![heading("Rename session")]) else {
        rename.area = Rect::default();
        return;
    };
    let field = Rect::new(content.x + 2, content.y + 2, rename.area.width, rows)
        .intersection(body(content, HINTS));
    rename.area = field;
    let layout = rename.layout();
    let top = layout.viewport_top(rename.scroll, field.height.into());
    let text = theme::text(theme::foreground()).bg(theme::background());
    let caret = theme::fixed::input_caret(text);
    let bar = theme::text(theme::primary());
    for (index, row) in layout
        .rows()
        .skip(top)
        .take(field.height.into())
        .enumerate()
    {
        let y = field.y + index as u16;
        f.buffer_mut().set_string(content.x, y, "▎", bar);
        let at = cursor_on.then(|| layout.caret_in(row)).flatten();
        let mut spans = Vec::new();
        let mut drawn = false;
        for cell in cells(row.text, row.column) {
            let on = at == Some(cell.byte);
            drawn |= on;
            spans.push(Span::styled(cell.shown(), if on { caret } else { text }));
        }
        if at.is_some() && !drawn {
            spans.push(Span::styled(" ", caret));
        }
        f.buffer_mut()
            .set_line(field.x, y, &Line::from(spans), field.width + 1);
    }
    rename.scroll = top;
    draw_hints(f, content, HINTS);
}

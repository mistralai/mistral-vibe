//! The add form: a label and an input per field, the focused label in the primary color; the instructions wrap over several rows.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::super::tab_cells::cells;
use crate::app::App;
use crate::loops::{Form, FormField, INSTRUCTIONS_ROWS};
use crate::ui::theme;

/// Rows the form takes: one per one-line field, and the instructions' wrapped rows.
pub(super) fn height(form: &Form, width: u16) -> u16 {
    let instructions = form
        .instructions_layout(text_width(input_width(width)))
        .height()
        .clamp(1, INSTRUCTIONS_ROWS as u16);
    FormField::ALL.len() as u16 - 1 + instructions
}

pub(super) fn draw(app: &mut App, f: &mut Frame, body: Rect) {
    let cursor_on = app.view.cursor_on;
    let Some(form) = app.loops.form.as_mut() else {
        return;
    };
    let muted = theme::muted_style().bg(theme::background());
    let focused_field = form.focused_field();
    let mut y = body.y;
    for which in FormField::ALL {
        let focused = which == focused_field;
        let label = if focused {
            theme::text(theme::primary())
        } else {
            muted
        };
        f.buffer_mut()
            .set_string(body.x, y, which.label(), label.bg(theme::background()));
        let width = input_width(body.width);
        let input = Rect::new(body.right() - width, y, width, 1);
        if which == FormField::Instructions && !form.instructions.text.is_empty() {
            form.width = text_width(width);
            y += draw_instructions(f, form, input, focused && cursor_on);
            continue;
        }
        let field = form.field(which);
        let placeholder = which.placeholder();
        crate::ui::proxy_setup::draw_field_or_placeholder(
            f,
            input,
            field,
            placeholder,
            focused,
            cursor_on,
        );
        y += 1;
    }
}

/// The input's width once the widest label and its gap are drawn.
fn input_width(width: u16) -> u16 {
    let label = FormField::ALL
        .iter()
        .map(|field| field.label().width())
        .max()
        .unwrap_or(0)
        + 2;
    width.saturating_sub(label as u16).max(1)
}

/// The instructions' wrap width: the input's last column is kept for a scroll arrow, beside the caret's.
fn text_width(input: u16) -> u16 {
    input.saturating_sub(1).max(1)
}

/// The wrapped instructions from `area`'s row, scrolled to keep the caret in view, a muted arrow on a row with lines hidden past it; the rows drawn.
fn draw_instructions(f: &mut Frame, form: &mut Form, area: Rect, caret: bool) -> u16 {
    let layout = form.instructions_layout(text_width(area.width));
    let top = layout.viewport_top(form.instructions.scroll, INSTRUCTIONS_ROWS);
    let total = usize::from(layout.height());
    let shown = total.min(INSTRUCTIONS_ROWS);
    let text = theme::text(theme::foreground()).bg(theme::background());
    let muted = theme::muted_style().bg(theme::background());
    let caret_style = theme::fixed::input_caret(text);
    for (index, row) in layout.rows().skip(top).take(shown).enumerate() {
        let y = area.y + index as u16;
        let at = caret.then(|| layout.caret_in(row)).flatten();
        let mut spans = Vec::new();
        let mut caret_drawn = false;
        for cell in cells(row.text, row.column) {
            let on_caret = at == Some(cell.byte);
            caret_drawn |= on_caret;
            spans.push(Span::styled(
                cell.shown(),
                if on_caret { caret_style } else { text },
            ));
        }
        if at.is_some() && !caret_drawn {
            spans.push(Span::styled(" ", caret_style));
        }
        f.buffer_mut()
            .set_line(area.x, y, &Line::from(spans), area.width);
        let arrow = match index {
            0 if top > 0 => Some("↑"),
            _ if index + 1 == shown && top + shown < total => Some("↓"),
            _ => None,
        };
        if let Some(arrow) = arrow {
            f.buffer_mut().set_string(area.right() - 1, y, arrow, muted);
        }
    }
    form.instructions.scroll = top;
    shown as u16
}

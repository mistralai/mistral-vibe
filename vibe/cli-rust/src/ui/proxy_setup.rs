//! `/proxy-setup` bottom-app: title, a label and input per variable, help line.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders};
use ratatui::Frame;

use super::vibe_code_project::paint;
use super::{scrollbar, theme};
use crate::app::App;
use crate::hints::{self, action, key};
use crate::proxy_setup::ProxyInput;

/// Borders, title and help line around a label and an input row per variable.
fn box_height(app: &App) -> u16 {
    app.proxy_setup.inputs.len() as u16 * 2 + 4
}

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    app.view.input_area = Rect::default();
    let kind = super::bottom_app::Kind::ProxySetup;
    super::bottom_app::draw(app, f, area, box_height(app), kind, draw_box);
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    app.proxy_setup.input_areas.clear();
    if area.width < 8 || area.height < 3 {
        return;
    }
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme::popup_border())),
        area,
    );
    let x = area.x + 2;
    let width = area.width - 4;
    let bottom = area.bottom() - 1;
    let heading = theme::text(theme::primary())
        .bg(theme::background())
        .add_modifier(Modifier::BOLD);
    f.buffer_mut().set_stringn(
        x,
        area.y + 1,
        "Proxy Configuration",
        width as usize,
        heading,
    );
    if area.height >= 4 {
        super::hint_line::draw_clipped(
            f,
            x,
            bottom - 1,
            width,
            &[
                (key::ARROWS, action::NAVIGATE),
                (key::ENTER, action::SAVE_EXIT),
                hints::CANCEL,
            ],
        );
    }
    let top = area.y + 2;
    let visible = bottom.saturating_sub(1).saturating_sub(top) as usize;
    draw_rows(app, f, Rect::new(x, top, width, visible as u16), heading);
}

/// The label/input rows in a `visible`-line window, with a scrollbar when they overflow.
fn draw_rows(app: &mut App, f: &mut Frame, rows: Rect, heading: Style) {
    let visible = rows.height as usize;
    let offset = app.proxy_setup.reconcile_scroll(visible);
    let total = app.proxy_setup.inputs.len() * 2;
    let cursor_on = app.view.cursor_on;
    let state = &mut app.proxy_setup;
    for line in offset..(offset + visible).min(total) {
        let y = rows.y + (line - offset) as u16;
        let index = line / 2;
        let input = &mut state.inputs[index];
        if line % 2 == 0 {
            f.buffer_mut()
                .set_stringn(rows.x, y, &input.key, rows.width as usize, heading);
            continue;
        }
        let text = Rect::new(rows.x + 2, y, rows.width.saturating_sub(2), 1);
        draw_input(f, rows.x, text, input, index == state.focused, cursor_on);
        state.input_areas.push((index, text));
    }
    if total > visible && visible > 0 {
        let bar = Rect::new(rows.right(), rows.y, 1, rows.height);
        let target = crate::mouse::MouseTarget::ProxySetup;
        scrollbar::draw(
            app,
            f,
            target,
            bar,
            total as u16,
            rows.height,
            offset as u16,
        );
    }
}

/// Python's `.proxy-input`: a `wide` left border, one cell of padding, the text.
fn draw_input(
    f: &mut Frame,
    x: u16,
    text: Rect,
    input: &mut ProxyInput,
    focused: bool,
    cursor_on: bool,
) {
    let bar = Style::default()
        .fg(theme::popup_border())
        .bg(theme::background());
    f.buffer_mut().set_string(x, text.y, "▎", bar);
    if !input.field.text.is_empty() {
        paint::field(f, text, &mut input.field, focused, cursor_on);
        return;
    }
    let placeholder = theme::muted_style().bg(theme::background());
    f.buffer_mut().set_stringn(
        text.x,
        text.y,
        &input.description,
        text.width as usize,
        placeholder,
    );
    if focused && cursor_on && text.width > 0 {
        let cell = Rect::new(text.x, text.y, 1, 1);
        f.buffer_mut()
            .set_style(cell, theme::fixed::input_caret(placeholder));
    }
}

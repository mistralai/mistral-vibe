//! `/mcp` browser bottom-app: title, source/tool option list, shortcut hint.

mod layout;
mod search;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{bottom_bar, loading, scrollbar, theme, transcript};
use crate::app::App;
use crate::mcp::{help_text, rows};
use layout::VisualLine;

/// Columns the option list loses to the border, the padding and its own gutter.
const GUTTER: u16 = 6;
/// One more column when the list overflows and reserves a scrollbar.
const SCROLLBAR_GUTTER: u16 = 7;

/// Draw the whole screen with the browser replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));

    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let lines = visual_lines(app, area);
    let chunks = super::bottom_app_chunks(
        app,
        area,
        loading_height,
        box_height(app, lines.len(), area.height),
    );

    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Blocked);
    draw_box(app, f, chunks[2], &lines);
    super::todo::draw_row(app, f, chunks[4]);
    bottom_bar::draw(app, f, chunks[3]);
}

/// Lay the rows out, reserving the scrollbar gutter once they overflow.
fn visual_lines(app: &App, area: Rect) -> Vec<VisualLine> {
    let rows = rows::rows(&app.mcp);
    let width = area.width.saturating_sub(GUTTER) as usize;
    let selected = if app.mcp.search.focused {
        usize::MAX
    } else {
        app.mcp.selected
    };
    let lines = layout::lines(&rows, selected, width);
    if lines.len() <= visible_lines(lines.len(), area.height) {
        return lines;
    }
    let width = area.width.saturating_sub(SCROLLBAR_GUTTER) as usize;
    layout::lines(&rows, selected, width)
}

/// Total box height: 2 borders + header + options + help.
fn box_height(app: &App, total: usize, area_height: u16) -> u16 {
    visible_lines(total, area_height) as u16 + header_height(app) + 3
}

/// Rows above the option list. The title always, plus the search row and the
/// blank its `margin-bottom` leaves; Python hides both in the detail view.
fn header_height(app: &App) -> u16 {
    if rows::viewing_source(&app.mcp).is_some() {
        1
    } else {
        3
    }
}

/// Visible option lines: `min(count, 50vh)` (Textual `max-height: 50vh`).
fn visible_lines(total: usize, area_height: u16) -> usize {
    (area_height as usize / 2).min(total)
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, lines: &[VisualLine]) {
    if area.height < 5 {
        return;
    }
    f.render_widget(Clear, area);
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let border = Style::default()
        .fg(theme::popup_border())
        .bg(theme::background());
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );

    f.buffer_mut().set_string(
        area.x + 2,
        area.y + 1,
        rows::title(&app.mcp),
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );

    let header = header_height(app);
    if header > 1 {
        search::draw(app, f, area);
    }
    let visible = area.height.saturating_sub(header + 3) as usize;
    let top = area.y + 1 + header;
    let offset = reconcile_scroll(app, lines, visible);
    let overflows = lines.len() > visible;
    // Remember where each option landed so a click can route to its row.
    app.mcp.list_area = Rect::new(
        area.x + 1,
        top,
        area.width.saturating_sub(2),
        visible as u16,
    );
    crate::mouse::register_region(app, app.mcp.list_area, crate::mouse::MouseTarget::Mcp);
    app.mcp.line_rows = lines.iter().map(|line| line.row).collect();

    for (row, line) in lines.iter().skip(offset).take(visible).enumerate() {
        draw_line(f, area, top + row as u16, line, overflows);
    }

    if overflows {
        let bar = Rect::new(area.x + area.width - 4, top, 1, visible as u16);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::Mcp,
            bar,
            lines.len() as u16,
            visible as u16,
            offset as u16,
        );
    }

    draw_help(app, f, area.x + 2, area.y + area.height - 2);
}

fn draw_line(f: &mut Frame, area: Rect, y: u16, line: &VisualLine, overflows: bool) {
    if line.highlighted {
        let gutter = if overflows { SCROLLBAR_GUTTER } else { GUTTER };
        let bar = Rect::new(area.x + 3, y, area.width.saturating_sub(gutter), 1);
        f.buffer_mut()
            .set_style(bar, Style::default().bg(theme::block_cursor_bg()));
    }
    let mut x = area.x + 3;
    for span in &line.spans {
        f.buffer_mut().set_string(x, y, &span.content, span.style);
        x += span.content.chars().count() as u16;
    }
}

/// Keep every line of the highlighted option visible (Textual `scroll_to_highlight`).
fn reconcile_scroll(app: &mut App, lines: &[VisualLine], visible: usize) -> usize {
    if visible == 0 {
        return 0;
    }
    let mut offset = app.mcp.scroll;
    if app.mcp.free_scroll {
        offset = offset.min(lines.len().saturating_sub(visible));
        app.mcp.scroll = offset;
        return offset;
    }
    if let Some(first) = lines.iter().position(|line| line.highlighted) {
        let last = lines
            .iter()
            .rposition(|line| line.highlighted)
            .unwrap_or(first);
        if first < offset {
            offset = first;
        } else if last >= offset + visible {
            offset = (last + 1 - visible).min(first);
        }
    }
    offset = offset.min(lines.len().saturating_sub(visible));
    app.mcp.scroll = offset;
    offset
}

/// The hint line: keys in bold $primary, labels in $text-muted.
fn draw_help(app: &App, f: &mut Frame, x: u16, y: u16) {
    let key = Style::default()
        .fg(theme::primary())
        .bg(theme::background())
        .add_modifier(Modifier::BOLD);
    let label = theme::muted_style().bg(theme::background());
    let mut cursor = x;
    for (shortcut, text) in help_text(app) {
        f.buffer_mut().set_string(cursor, y, shortcut, key);
        cursor += shortcut.chars().count() as u16;
        f.buffer_mut().set_string(cursor, y, text, label);
        cursor += text.chars().count() as u16;
    }
}

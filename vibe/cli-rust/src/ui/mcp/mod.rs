//! `/mcp` browser bottom-app: title, source/tool option list, shortcut hint.

pub(crate) mod layout;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{hint_line, list_scroll, scrollbar, search_field, theme};
use crate::app::App;
use crate::mcp::{help_text, rows};
use layout::VisualLine;

/// Columns the option list loses to the border, the padding and its own gutter.
const GUTTER: u16 = 6;
/// One more column when the list overflows and reserves a scrollbar.
const SCROLLBAR_GUTTER: u16 = 7;

/// Draw the whole screen with the browser replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let lines = visual_lines(app, area);
    let height = box_height(app, lines.len(), area.height);
    let kind = super::bottom_app::Kind::Mcp;
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, &lines)
    });
}

/// Lay the rows out, reserving the scrollbar gutter once they overflow.
fn visual_lines(app: &App, area: Rect) -> Vec<VisualLine> {
    let rows = rows::rows(&app.mcp);
    let width = area.width.saturating_sub(GUTTER) as usize;
    let selected = app.mcp.selected;
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
        draw_search(app, f, area);
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

    hint_line::draw(f, area.x + 2, area.y + area.height - 2, &help_text(app));
}

fn draw_line(f: &mut Frame, area: Rect, y: u16, line: &VisualLine, overflows: bool) {
    if line.highlighted {
        let gutter = if overflows { SCROLLBAR_GUTTER } else { GUTTER };
        let bar = Rect::new(area.x + 3, y, area.width.saturating_sub(gutter), 1);
        super::list_cursor::paint(f, bar);
    }
    let mut x = area.x + 3;
    for span in &line.spans {
        f.buffer_mut().set_string(x, y, &span.content, span.style);
        x += span.content.chars().count() as u16;
    }
}

/// Keep the highlighted option visible, the wheel's free scroll aside.
fn reconcile_scroll(app: &mut App, lines: &[VisualLine], visible: usize) -> usize {
    let max = lines.len().saturating_sub(visible);
    let offset = match app.mcp.free_scroll {
        true => app.mcp.scroll.min(max),
        false => {
            let first = lines.iter().position(|line| line.highlighted);
            let last = lines.iter().rposition(|line| line.highlighted);
            let highlight = first
                .zip(last)
                .map_or(0..0, |(first, last)| first..last + 1);
            list_scroll::follow(app.mcp.scroll, visible, lines.len(), highlight, |line| {
                lines[line].selectable
            })
        }
    };
    app.mcp.scroll = offset;
    offset
}

/// The search row under the title; the field owns its mouse like Python's `Input`.
fn draw_search(app: &mut App, f: &mut Frame, area: Rect) {
    let row = Rect::new(area.x + 3, area.y + 2, area.width.saturating_sub(6), 1);
    let input = search_field::draw_row(
        f,
        row,
        &app.mcp.search,
        "Search servers and connectors",
        app.view.cursor_on,
        theme::background(),
    );
    app.mcp.search.area = input;
    crate::mouse::register_region(app, input, crate::mouse::MouseTarget::Mcp);
    // A box drag never selects the field.
    let field = (input.y, input.x, input.right().saturating_sub(1));
    app.view.bottom_app_selection_chrome.push(field);
}

//! `/plugins` browser bottom-app: title, search field, plugin option list, shortcut hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::Span;
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;
use unicode_width::UnicodeWidthChar;

use super::mcp::layout::{merge, text, wrap, Sc};
use super::{list_scroll, scrollbar, theme};
use crate::app::App;
use crate::mouse::MouseTarget;
use crate::plugins::rows::{self, Row};
use crate::plugins::text::entry_facts;
use crate::server::PluginCatalogEntry;

/// Columns the option list loses to the border, the padding and its own gutter.
const GUTTER: u16 = 6;
/// One more column when the list overflows and reserves a scrollbar.
const SCROLLBAR_GUTTER: u16 = 7;

struct Line {
    row: usize,
    highlighted: bool,
    selectable: bool,
    spans: Vec<Span<'static>>,
}

/// Draw the whole screen with the browser replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let max_visible = area.height as usize / 2;
    let lines = visual_lines(app, area.width, max_visible);
    let visible = lines.len().min(max_visible) as u16;
    let kind = super::bottom_app::Kind::Plugins;
    super::bottom_app::draw(app, f, area, visible + chrome(app), kind, |app, f, area| {
        draw_box(app, f, area, &lines)
    });
}

/// Rows around the list: borders, title and its blank, the blank above help,
/// help, and the list view's search row.
fn chrome(app: &App) -> u16 {
    6 + u16::from(search_shown(app))
}

/// The list view shows its search row under the title, like `/mcp` and `/config`.
fn search_shown(app: &App) -> bool {
    app.plugins.viewing.is_none()
}

/// Lay the rows out for `visible` lines (Textual `max-height: 50vh`), reserving the scrollbar once they overflow.
fn visual_lines(app: &App, width: u16, visible: usize) -> Vec<Line> {
    let lines = lay_out(app, width.saturating_sub(GUTTER) as usize);
    if lines.len() <= visible {
        return lines;
    }
    lay_out(app, width.saturating_sub(SCROLLBAR_GUTTER) as usize)
}

/// Plugin rows stay on one line (Python `no_wrap`); notes and facts wrap.
fn lay_out(app: &App, width: usize) -> Vec<Line> {
    let state = &app.plugins;
    let plugins = &state.catalog.plugins;
    let rows = rows::rows(state);
    let name_width = rows
        .iter()
        .filter_map(|row| match row {
            Row::Entry(index) => Some(plugins[*index].name.chars().count()),
            _ => None,
        })
        .max()
        .unwrap_or(0);
    let mut out = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let highlighted = index == state.selected && row.selectable();
        let (base, dim) = styles(highlighted);
        let wrapped = match row {
            Row::Entry(entry) => {
                let mut chars = entry_chars(&plugins[*entry], name_width, base, dim);
                chars.truncate(width);
                vec![chars]
            }
            Row::Note(note) => wrap_indented(note, disabled(base), width),
            Row::Blank => vec![Vec::new()],
            Row::Heading(heading) => {
                let bold = disabled(base).add_modifier(Modifier::BOLD);
                wrap_indented(heading, bold, width)
            }
            Row::Dim(line) => wrap_indented(line, dim, width),
        };
        out.extend(wrapped.into_iter().map(|chars| Line {
            row: index,
            highlighted,
            selectable: row.selectable(),
            spans: merge(&chars),
        }));
    }
    out
}

/// Wrap with a hanging indent, folding words longer than a line (Rich `overflow=fold`).
fn wrap_indented(line: &str, style: Style, width: usize) -> Vec<Vec<Sc>> {
    let body = line.trim_start_matches(' ');
    let indent = text(&line[..line.len() - body.len()], style);
    let room = width.saturating_sub(indent.len()).max(1);
    wrap(&text(body, style), room)
        .into_iter()
        .flat_map(|row| fold(row, room))
        .map(|row| [indent.clone(), row].concat())
        .collect()
}

/// Split a row into chunks of at most `room` terminal columns, so wide glyphs fold too.
fn fold(row: Vec<Sc>, room: usize) -> Vec<Vec<Sc>> {
    let mut out = vec![Vec::new()];
    let mut used = 0;
    for sc in row {
        let width = sc.0.width().unwrap_or(0);
        if used + width > room && used > 0 {
            out.push(Vec::new());
            used = 0;
        }
        if let Some(chunk) = out.last_mut() {
            chunk.push(sc);
        }
        used += width;
    }
    out
}

/// Disabled options take `$text-disabled` (Textual `option-list--option-disabled`).
fn disabled(base: Style) -> Style {
    base.fg(theme::muted())
}

/// The block cursor bolds the whole highlighted option, dim facts included.
fn styles(highlighted: bool) -> (Style, Style) {
    super::list_cursor::styles(highlighted)
}

/// `  name  scope · format · digest`, plus drift and uninstall notes (Python `_entry_label`).
fn entry_chars(entry: &PluginCatalogEntry, width: usize, base: Style, dim: Style) -> Vec<Sc> {
    let mut chars = text(&format!("  {:<width$}", entry.name), base);
    chars.extend(text(&format!("  {}", entry_facts(entry)), dim));
    if entry.drifted > 0 {
        let warning = base.fg(theme::text_warning());
        chars.extend(text(&format!(" · ⚠ {} drifted", entry.drifted), warning));
    }
    if entry.installed_root.is_none() {
        chars.extend(text(" · uninstalled since pin", dim));
    }
    chars
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, lines: &[Line]) {
    if area.height < chrome(app) {
        return;
    }
    f.render_widget(Clear, area);
    let background = Style::default().bg(theme::background());
    f.buffer_mut().set_style(area, background);
    let border = background.fg(theme::popup_border());
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );
    let title = background.fg(theme::primary()).add_modifier(Modifier::BOLD);
    let title_width = area.width.saturating_sub(4) as usize;
    f.buffer_mut().set_stringn(
        area.x + 2,
        area.y + 1,
        rows::title(&app.plugins),
        title_width,
        title,
    );

    if search_shown(app) {
        draw_search(app, f, area);
    }
    let visible = area.height.saturating_sub(chrome(app)) as usize;
    let top = area.y + 3 + u16::from(search_shown(app));
    let offset = reconcile_scroll(app, lines, visible);
    let overflows = lines.len() > visible;
    app.plugins.list_area = Rect::new(
        area.x + 1,
        top,
        area.width.saturating_sub(2),
        visible as u16,
    );
    crate::mouse::register_region(app, app.plugins.list_area, MouseTarget::Plugins);
    app.plugins.line_rows = lines.iter().map(|line| line.row).collect();
    for (row, line) in lines.iter().skip(offset).take(visible).enumerate() {
        draw_line(f, area, top + row as u16, line, overflows);
    }
    if overflows {
        let bar = Rect::new(area.x + area.width - 4, top, 1, visible as u16);
        let (total, shown) = (lines.len() as u16, visible as u16);
        scrollbar::draw(
            app,
            f,
            MouseTarget::Plugins,
            bar,
            total,
            shown,
            offset as u16,
        );
    }
    let help = Rect::new(
        area.x + 2,
        area.y + area.height - 2,
        area.width.saturating_sub(4),
        1,
    );
    draw_help(app, f, help);
}

/// The search row under the title; the field owns its mouse like Python's `Input`.
fn draw_search(app: &mut App, f: &mut Frame, area: Rect) {
    let row = Rect::new(area.x + 3, area.y + 2, area.width.saturating_sub(6), 1);
    let input = super::search_field::draw_row(
        f,
        row,
        &app.plugins.filter,
        "Search plugins",
        app.view.cursor_on,
        theme::background(),
    );
    app.plugins.filter.area = input;
    crate::mouse::register_region(app, input, MouseTarget::Plugins);
    // A box drag never selects the field.
    let field = (input.y, input.x, input.right().saturating_sub(1));
    app.view.bottom_app_selection_chrome.push(field);
}

/// One option line, clipped to the option column so wide glyphs never reach the border.
fn draw_line(f: &mut Frame, area: Rect, y: u16, line: &Line, overflows: bool) {
    let gutter = if overflows { SCROLLBAR_GUTTER } else { GUTTER };
    let bar = Rect::new(area.x + 3, y, area.width.saturating_sub(gutter), 1);
    if line.highlighted {
        super::list_cursor::paint(f, bar);
    }
    let mut x = bar.x;
    for span in &line.spans {
        let room = bar.right().saturating_sub(x) as usize;
        x = f
            .buffer_mut()
            .set_stringn(x, y, &span.content, room, span.style)
            .0;
    }
}

/// Keep the highlighted option visible, the wheel's free scroll aside.
fn reconcile_scroll(app: &mut App, lines: &[Line], visible: usize) -> usize {
    let max = lines.len().saturating_sub(visible);
    let offset = match app.plugins.free_scroll {
        true => app.plugins.scroll.min(max),
        false => {
            let first = lines.iter().position(|line| line.highlighted);
            let last = lines.iter().rposition(|line| line.highlighted);
            let highlight = first
                .zip(last)
                .map_or(0..0, |(first, last)| first..last + 1);
            list_scroll::follow(
                app.plugins.scroll,
                visible,
                lines.len(),
                highlight,
                |line| lines[line].selectable,
            )
        }
    };
    app.plugins.scroll = offset;
    offset
}

/// The hint line, clipped to the box.
fn draw_help(app: &App, f: &mut Frame, area: Rect) {
    super::hint_line::draw_clipped(f, area.x, area.y, area.width, &rows::help(&app.plugins));
}

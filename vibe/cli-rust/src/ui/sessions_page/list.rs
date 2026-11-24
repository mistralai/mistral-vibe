//! The grouped session rows: state marker, title, branch, time.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::super::{list_cursor, scrollbar, theme};
use crate::app::App;
use crate::mouse::MouseTarget;
use crate::sessions_page::model::{Branch, Line};
use crate::sessions_page::{Session, Sort};
use crate::utils::text::ellipsize;

use super::row_text::{extras, layout, relative_time};

pub(super) fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    if app.sessions_page.view.rows.is_empty() {
        // No row is painted: drop the last layout's hits, or the hover tint
        // would draw a row that is no longer there.
        app.sessions_page.browse.list.layout(&[], area);
    }
    let page = &app.sessions_page;
    if page.view.rows.is_empty() {
        if page.loading {
            return loader(app, f, area);
        }
        let browse = &page.browse;
        let text = match (browse.archived, browse.search.query.is_empty()) {
            (_, false) => "No sessions match.",
            (true, _) => "No archived sessions.",
            _ if browse.folder_only => "No sessions in this folder.",
            _ => "No sessions yet.",
        };
        f.buffer_mut()
            .set_string(area.x, area.y, text, theme::muted_style());
        return;
    }
    let options = page.view.options();
    let total = options.len();
    let rows = Rect {
        width: area.width.saturating_sub(1),
        ..area
    };
    let (scroll, lines_area) = scroll_with_pin(app, &options, rows);
    let pinned = lines_area.y > rows.y;
    crate::mouse::register_region(app, rows, MouseTarget::SessionsPage);
    let mut chrome = Vec::new();
    let page = &app.sessions_page;
    let selected = page.browse.list.selected();
    if pinned {
        if let Some((label, count)) = header_above(&page.view.lines, scroll + 1) {
            header(f, Rect::new(rows.x, rows.y, rows.width, 1), label, count);
        }
    }
    for (offset, line) in page
        .view
        .lines
        .iter()
        .skip(scroll)
        .take(lines_area.height.into())
        .enumerate()
    {
        let y = lines_area.y + offset as u16;
        let row = Rect::new(rows.x, y, rows.width, 1);
        match line {
            Line::Header { label, count } => header(f, row, label, *count),
            Line::Blank => {}
            Line::Row {
                option,
                session,
                branch,
                folded,
            } => {
                // The cursor and state marks are chrome: a copy reads the text.
                let marks = 3 + 2 * u16::from(branch.is_some());
                chrome.push((y, row.x, row.x + marks));
                let session = &page.sessions[*session];
                let state = RowState {
                    highlighted: *option == selected,
                    current: app.session.session_id.as_deref() == Some(session.id.as_str()),
                    branch: *branch,
                    folded: *folded,
                };
                draw_row(app, f, row, session, state);
            }
        }
    }
    if total > usize::from(area.height) {
        let bar = Rect::new(area.right() - 1, area.y, 1, area.height);
        chrome.extend((bar.y..bar.bottom()).map(|y| (y, bar.x, bar.x)));
        let visible = usize::from(area.height);
        scrollbar::draw_large(
            app,
            f,
            MouseTarget::SessionsPage,
            bar,
            total,
            visible,
            scroll,
        );
    }
    app.view.bottom_app_selection_chrome.extend(chrome);
}

/// The scroll offset and the rows the lines take. A group scrolled past its
/// header keeps that header pinned on the first row: the lines then start a
/// row lower, laid out again so the pin hides none of them. When that second
/// layout lands on a header or a gap, there is nothing to pin.
fn scroll_with_pin(app: &mut App, options: &[Option<usize>], rows: Rect) -> (usize, Rect) {
    let starts_a_group = |app: &App, scroll: usize| {
        matches!(
            app.sessions_page.view.lines.get(scroll),
            Some(Line::Header { .. } | Line::Blank)
        )
    };
    let scroll = app.sessions_page.browse.list.layout(options, rows);
    if scroll == 0 || rows.height < 2 || starts_a_group(app, scroll) {
        return (scroll, rows);
    }
    let below = Rect {
        y: rows.y + 1,
        height: rows.height - 1,
        ..rows
    };
    let scroll = app.sessions_page.browse.list.layout(options, below);
    if starts_a_group(app, scroll) {
        return (app.sessions_page.browse.list.layout(options, rows), rows);
    }
    (scroll, below)
}

/// The header of the group the line at `index` belongs to, when that header
/// is above it.
fn header_above(lines: &[Line], index: usize) -> Option<(&str, usize)> {
    lines[..index].iter().rev().find_map(|line| match line {
        Line::Header { label, count } => Some((label.as_str(), *count)),
        _ => None,
    })
}

/// The empty list while it loads: Vibe's own spinner and gradient on the
/// list's last row, right above the hints, where the conversation's loader
/// sits above its input; it says what it waits on, the app server first.
fn loader(app: &App, f: &mut Frame, area: Rect) {
    let label = match app.sessions_page.awaiting_server {
        true => "Starting Vibe",
        false => "Loading sessions",
    };
    let line = ratatui::text::Line::from(app.view.loading.spans(label, "…"));
    let y = area.bottom().saturating_sub(1).max(area.y);
    f.buffer_mut().set_line(area.x, y, &line, area.width);
}

/// A group header: its label cut to leave room for the count, never past the row.
fn header(f: &mut Frame, row: Rect, label: &str, count: usize) {
    let bold = theme::text(theme::text_secondary()).add_modifier(Modifier::BOLD);
    let count = count.to_string();
    let room = usize::from(row.width).saturating_sub(count.width() + 2);
    let label = ellipsize(label, room);
    let buffer = f.buffer_mut();
    buffer.set_stringn(row.x, row.y, &label, row.width.into(), bold);
    let x = row.x + label.width() as u16 + 2;
    if x < row.right() {
        let left = usize::from(row.right() - x);
        buffer.set_stringn(x, row.y, &count, left, theme::muted_style());
    }
}

struct RowState {
    highlighted: bool,
    current: bool,
    branch: Option<Branch>,
    folded: usize,
}

fn draw_row(app: &App, f: &mut Frame, row: Rect, session: &Session, state: RowState) {
    let (text, dim) = list_cursor::styles(state.highlighted);
    if state.highlighted {
        list_cursor::paint(f, row);
    }
    let buffer = f.buffer_mut();
    let mut x = row.x;
    let marker = list_cursor::marker(state.current);
    buffer.set_string(
        x,
        row.y,
        marker,
        list_cursor::marker_style(text, state.current),
    );
    x += 2;
    let (glyph, color) = marker_glyph(app, session, state.current);
    buffer.set_string(x, row.y, glyph, list_cursor::accent(text, color));
    x += 2;
    if let Some(branch) = state.branch {
        let connector = match branch {
            Branch::Middle => "├ ",
            Branch::Last => "└ ",
        };
        buffer.set_string(x, row.y, connector, dim);
        x += 2;
    }
    let time = relative_time(match app.sessions_page.browse.sort {
        Sort::Activity => session.activity(),
        Sort::Created => session.created_at,
    });
    let label = session.label().to_owned();
    let width = usize::from(row.right().saturating_sub(x));
    let line = layout(&label, &extras(session, state.folded), &time, width);
    let mut cursor = x;
    for (part, dimmed) in line {
        let left = usize::from(row.right().saturating_sub(cursor));
        let style = if dimmed { dim } else { text };
        buffer.set_stringn(cursor, row.y, &part, left, style);
        cursor += part.width() as u16;
    }
}

/// The row's one state marker: see [`super::legend::State`].
fn marker_glyph(
    app: &App,
    session: &Session,
    current: bool,
) -> (&'static str, ratatui::style::Color) {
    let state = super::legend::State::of(app, session, current);
    (state.glyph(), state.color())
}

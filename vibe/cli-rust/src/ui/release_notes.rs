//! The `/release-notes` screen: search row, release notes, full-changelog link, hints.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::{scrollbar, theme};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::mouse::MouseTarget;
use crate::release_notes::FULL_CHANGELOG_URL;
use crate::search_field;
use crate::utils::text::ellipsize;

const HELP: &[Hint] = &[
    (key::NAV, action::SCROLL),
    hints::SEARCH,
    (key::ENTER, action::FULL_CHANGELOG),
    hints::CLOSE,
];
const LINK_LABEL: &str = "Full changelog: ";
const MAX_WIDTH: u16 = 100;
const NO_MATCH: &str = "No release notes match your search.";

/// Draw the modal over `area` (the full screen); the base UI shows through the margins.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    app.view.cursor_position = None;
    let w = ((area.width as u32 * 96 / 100) as u16).min(MAX_WIDTH);
    let h = (area.height as u32 * 96 / 100) as u16;
    if w < 20 || h < 11 {
        let text = "Enlarge terminal to browse the release notes. Esc close";
        super::config_edit::draw_too_small(app, f, area, text);
        return;
    }
    let bx = area.x + (area.width - w) / 2;
    let by = area.y + (area.height - h) / 2;
    let box_area = Rect::new(bx, by, w, h);
    crate::mouse::register_region(app, area, MouseTarget::Blocked);

    f.render_widget(Clear, box_area);
    let surface = Style::default().bg(theme::background());
    f.buffer_mut().set_style(box_area, surface);
    let primary = surface.fg(theme::primary());
    let title = Line::from(vec![
        Span::styled("─", primary),
        Span::styled(" Release notes ", primary.add_modifier(Modifier::BOLD)),
    ]);
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(primary)
        .title(title);
    f.render_widget(block, box_area);

    let cx = bx + 3;
    draw_search(app, f, Rect::new(cx, by + 2, w - 6, 1));
    let body = Rect::new(bx + 1, by + 4, w - 4, h.saturating_sub(9));
    draw_body(app, f, body);
    draw_link(app, f, Rect::new(cx, by + h - 4, w - 6, 1));
    let search = &app.release_notes.search;
    let help = search_field::hints(search.focused, !search.query.is_empty(), HELP);
    super::hint_line::draw_clipped(f, cx, by + h - 2, w - 6, &help);
}

/// The full-changelog link under the notes; the URL stays readable to copy without a click.
fn draw_link(app: &mut App, f: &mut Frame, row: Rect) {
    let muted = theme::muted_style().bg(theme::background());
    let link = muted.add_modifier(Modifier::UNDERLINED);
    let (x, _) = f
        .buffer_mut()
        .set_stringn(row.x, row.y, LINK_LABEL, row.width as usize, muted);
    let room = row.right().saturating_sub(x);
    let url = ellipsize(FULL_CHANGELOG_URL, room as usize);
    f.buffer_mut().set_string(x, row.y, &url, link);
    let area = Rect::new(x, row.y, (url.width() as u16).min(room), 1);
    app.release_notes.link_area = area;
    crate::mouse::register_region(app, area, MouseTarget::ReleaseNotes);
}

fn draw_search(app: &mut App, f: &mut Frame, row: Rect) {
    let input = super::search_field::draw_row(
        f,
        row,
        &app.release_notes.search,
        "Search release notes",
        app.view.cursor_on,
        theme::background(),
    );
    app.release_notes.search.area = input;
    crate::mouse::register_region(app, input, MouseTarget::ReleaseNotes);
}

/// The release notes from the current scroll, with a scrollbar when they overflow.
fn draw_body(app: &mut App, f: &mut Frame, body: Rect) {
    let region = Rect::new(body.x, body.y, body.width + 1, body.height);
    crate::mouse::register_region(app, region, MouseTarget::ReleaseNotes);
    let state = &mut app.release_notes;
    let total = state.lines(body.width).len();
    state.page = body.height as usize;
    state.total = total;
    state.scroll = state.scroll.min(total.saturating_sub(state.page));
    let scroll = state.scroll;
    if total == 0 {
        let muted = theme::muted_style().bg(theme::background());
        f.buffer_mut()
            .set_stringn(body.x + 2, body.y, NO_MATCH, body.width as usize, muted);
        return;
    }
    let lines = app.release_notes.lines(body.width);
    for (row, line) in lines
        .iter()
        .skip(scroll)
        .take(body.height as usize)
        .enumerate()
    {
        f.buffer_mut()
            .set_line(body.x, body.y + row as u16, line, body.width);
    }
    if total > body.height as usize {
        let bar = Rect::new(body.right(), body.y, 1, body.height);
        scrollbar::draw_large(
            app,
            f,
            MouseTarget::ReleaseNotes,
            bar,
            total,
            body.height as usize,
            scroll,
        );
    }
}

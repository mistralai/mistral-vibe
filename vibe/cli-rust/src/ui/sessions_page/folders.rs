//! The folders column: All, then one row per folder with its most urgent mark and its count.

use ratatui::layout::Rect;
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::super::list_cursor;
use super::legend::State;
use crate::app::App;
use crate::sessions_page::Column;
use crate::utils::text::ellipsize;

/// The column's width, rule excluded.
pub(super) const WIDTH: u16 = 24;

pub(super) fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let page = &app.sessions_page;
    let total: usize = page.view.folders.iter().map(|f| f.sessions.len()).sum();
    let mut rows = vec![(
        "All".to_owned(),
        mark(app, 0..page.view.folders.len()),
        total,
    )];
    for (index, folder) in page.view.folders.iter().enumerate() {
        rows.push((
            folder.label.clone(),
            mark(app, index..index + 1),
            folder.sessions.len(),
        ));
    }
    let chosen = page.view.folder.map_or(0, |index| index + 1);
    let focused = page.browse.focus == Column::Folders;
    let scroll = app
        .sessions_page
        .browse
        .folders
        .layout_rows(rows.len(), area);
    for (offset, (label, state, count)) in rows.iter().enumerate().skip(scroll) {
        let y = area.y + (offset - scroll) as u16;
        if y >= area.bottom() {
            break;
        }
        let row = Rect::new(area.x, y, area.width, 1);
        let highlighted = focused && offset == chosen;
        if highlighted {
            list_cursor::paint(f, row);
        }
        let (text, dim) = list_cursor::styles(highlighted);
        let buffer = f.buffer_mut();
        let picked = offset == chosen;
        buffer.set_string(
            row.x,
            y,
            list_cursor::marker(picked),
            list_cursor::marker_style(text, picked),
        );
        let count = count.to_string();
        let tail = 2 + count.width();
        let room = usize::from(area.width).saturating_sub(2 + tail + 1);
        buffer.set_stringn(row.x + 2, y, ellipsize(label, room), room, text);
        let x = row.right().saturating_sub(tail as u16);
        if let Some(state) = state {
            buffer.set_string(
                x,
                y,
                state.glyph(),
                list_cursor::accent(text, state.color()),
            );
        }
        buffer.set_string(x + 2, y, &count, dim);
    }
}

/// The most urgent state among the sessions of `folders`.
fn mark(app: &App, folders: std::ops::Range<usize>) -> Option<State> {
    let page = &app.sessions_page;
    let current = app.session.session_id.as_deref();
    page.view.folders[folders]
        .iter()
        .flat_map(|folder| &folder.sessions)
        .map(|&index| {
            let session = &page.sessions[index];
            State::of(app, session, current == Some(session.id.as_str()))
        })
        .min()
}

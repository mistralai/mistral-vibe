//! The `/loop` rows: title, recurrence and next run, in aligned columns.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use crate::app::App;
use crate::loops::{rows, schedule};
use crate::mouse::MouseTarget;
use crate::ui::{list_cursor, scrollbar};
use crate::utils::text::ellipsize;

const GAP: &str = "  ";
const DELETING: &str = "deleting…";

pub(super) fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let now = crate::clock::unix_now_seconds();
    let cells: Vec<[String; 3]> = rows(app)
        .into_iter()
        .map(|scheduled| {
            let next = match app.loops.deleting.as_deref() == Some(scheduled.id.as_str()) {
                true => DELETING.to_owned(),
                false => schedule::next_run(scheduled.next_fire_at - now),
            };
            [
                schedule::title(scheduled).to_owned(),
                schedule::recurrence(scheduled),
                next,
            ]
        })
        .collect();
    let total = cells.len();
    let has_scrollbar = total > usize::from(area.height);
    let options = Rect {
        width: area.width.saturating_sub(u16::from(has_scrollbar) * 2),
        ..area
    };
    let offset = app.loops.list.layout_rows(total, options);
    let hit = Rect {
        x: area.x.saturating_sub(1),
        width: area.width + 2,
        ..area
    };
    crate::mouse::register_region(app, hit, MouseTarget::Loops);
    let widths = widths(&cells, usize::from(options.width));
    let selected = app.loops.list.selected();
    for (row, index) in (offset..total).take(usize::from(area.height)).enumerate() {
        let y = area.y + row as u16;
        let highlighted = index == selected;
        if highlighted {
            list_cursor::paint(
                f,
                Rect {
                    y,
                    height: 1,
                    ..options
                },
            );
        }
        let line = Line::from(spans(&cells[index], widths, highlighted));
        f.buffer_mut().set_line(options.x, y, &line, options.width);
    }
    if has_scrollbar {
        let bar = Rect::new(area.right() - 1, area.y, 1, area.height);
        let target = MouseTarget::Loops;
        scrollbar::draw(
            app,
            f,
            target,
            bar,
            total as u16,
            area.height,
            offset as u16,
        );
    }
}

/// `title  recurrence  next`, the title in the row's text style and the rest dimmed.
fn spans(cells: &[String; 3], widths: [usize; 3], highlighted: bool) -> Vec<Span<'static>> {
    let (text, dim) = list_cursor::styles(highlighted);
    cells
        .iter()
        .zip(widths)
        .enumerate()
        .flat_map(|(column, (cell, width))| {
            let style = if column == 0 { text } else { dim };
            let gap = if column == 0 { "" } else { GAP };
            let cell = list_cursor::padded(&ellipsize(cell, width), width);
            [Span::styled(gap, text), Span::styled(cell, style)]
        })
        .collect()
}

/// The recurrence and next-run columns keep their width; the title takes the rest.
fn widths(cells: &[[String; 3]], max: usize) -> [usize; 3] {
    let widest = |column: usize| {
        cells
            .iter()
            .map(|row| row[column].width())
            .max()
            .unwrap_or(0)
    };
    let (recurrence, next) = (widest(1), widest(2).max(DELETING.width()));
    let title = widest(0).min(max.saturating_sub(recurrence + next + 2 * GAP.width()));
    [title, recurrence, next]
}

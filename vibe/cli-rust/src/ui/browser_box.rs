//! The box `/plugins` and `/skills` share: frame, search row, option list, lines.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::Span;
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::bottom_app::content;
use super::visual_lines::VisualLine;
use super::{hint_line, list_cursor, scrollbar, theme};
use crate::app::App;
use crate::hints::Hint;
use crate::mouse::MouseTarget;
use crate::search_field::Search;

/// Columns the option list loses to the border, the padding and its own gutter.
pub(crate) const GUTTER: u16 = 6;
/// One more column when the list overflows and reserves a scrollbar.
pub(crate) const SCROLLBAR_GUTTER: u16 = 7;
/// The box width from which a preview sits beside the list instead of under it, each half wide enough for a list's columns.
pub(crate) const SIDE_BY_SIDE: u16 = 140;

/// A box split into its left and right halves, each laid out with the margins of a whole box.
pub(crate) fn halves(area: Rect) -> (Rect, Rect) {
    let left = area.width / 2;
    let right = Rect::new(area.x + left, area.y, area.width - left, area.height);
    (Rect::new(area.x, area.y, left, area.height), right)
}

/// A selection in a box split in halves keeps to the half it started in: the left one up to the right one's edge, the right one inside its own padding.
pub(crate) fn split_selection(app: &mut App, (left, right): (Rect, Rect)) {
    let text = content(left);
    let left = Rect {
        width: right.x.saturating_sub(text.x),
        ..text
    };
    app.view.bottom_app_selection_halves = Some((left, content(right)));
}

/// Rows the box spends around its lines: borders, title and its blank, help and the blank above it, and the search row.
pub(crate) fn chrome(search: bool) -> u16 {
    6 + u16::from(search)
}

/// `height` full-width rows starting `from` lines under the title, or under the search row when shown.
pub(crate) fn pane(area: Rect, search: bool, from: u16, height: u16) -> Rect {
    let top = area.y + 3 + u16::from(search) + from;
    Rect::new(area.x + 1, top, area.width.saturating_sub(2), height)
}

/// The hint row along the bottom of the box.
pub(crate) fn draw_help(f: &mut Frame, area: Rect, help: &[Hint]) {
    let (x, y) = (area.x + 2, area.y + area.height - 2);
    hint_line::draw_clipped(f, x, y, area.width.saturating_sub(4), help);
}

/// Lay lines out for `visible` rows at most (`50vh`), reserving the scrollbar once they overflow.
pub(crate) fn fit<L>(width: u16, visible: usize, lay_out: impl Fn(usize) -> Vec<L>) -> Vec<L> {
    let lines = lay_out(width.saturating_sub(GUTTER) as usize);
    if lines.len() <= visible {
        return lines;
    }
    lay_out(width.saturating_sub(SCROLLBAR_GUTTER) as usize)
}

/// Clear the box, fill its background, and draw its border and title.
pub(crate) fn draw_frame(f: &mut Frame, area: Rect, title: &str) {
    f.render_widget(Clear, area);
    let background = Style::default().bg(theme::background());
    f.buffer_mut().set_style(area, background);
    let border = theme::border_style().bg(theme::background());
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );
    let style = background.fg(theme::primary()).add_modifier(Modifier::BOLD);
    let width = area.width.saturating_sub(4) as usize;
    f.buffer_mut()
        .set_stringn(area.x + 2, area.y + 1, title, width, style);
}

/// Columns between the title and the first tab, and between tabs.
const TAB_GAP: u16 = 2;

/// Tabs after the title like the question app's, the open one in brackets and alone when all do not fit; each tab's area, for clicks.
pub(crate) fn draw_tabs<T: Copy + PartialEq>(
    f: &mut Frame,
    area: Rect,
    title: &str,
    tabs: &[(T, String)],
    open: T,
) -> Vec<(T, Rect)> {
    let right = area.right().saturating_sub(2);
    let y = area.y + 1;
    let mut x = area.x + 2 + title.width() as u16;
    let shown = tab_texts(tabs, open);
    let needed: u16 = shown
        .iter()
        .map(|(_, text)| TAB_GAP + text.width() as u16)
        .sum();
    let all_fit = x + needed <= right;
    let mut areas = Vec::new();
    for (tab, text) in shown {
        let width = text.width() as u16;
        if (!all_fit && tab != open) || x + TAB_GAP + width > right {
            continue;
        }
        x += TAB_GAP;
        f.buffer_mut()
            .set_string(x, y, &text, theme::text(theme::primary()));
        areas.push((tab, Rect::new(x, y, width, 1)));
        x += width;
    }
    areas
}

/// Each tab's text: the open one in brackets, the others padded to the same width.
fn tab_texts<T: Copy + PartialEq>(tabs: &[(T, String)], open: T) -> Vec<(T, String)> {
    let text = |tab: T, label: &str| match tab == open {
        true => format!("[{label}]"),
        false => format!(" {label} "),
    };
    tabs.iter()
        .map(|(tab, label)| (*tab, text(*tab, label)))
        .collect()
}

/// The search row under the title; the field owns its mouse, and a box drag never selects it.
pub(crate) fn draw_search(
    app: &mut App,
    f: &mut Frame,
    area: Rect,
    field: fn(&mut App) -> &mut Search,
    placeholder: &str,
    target: MouseTarget,
) {
    let row = Rect::new(area.x + 3, area.y + 2, area.width.saturating_sub(6), 1);
    let cursor_on = app.view.cursor_on;
    let background = theme::background();
    let input =
        super::search_field::draw_row(f, row, field(app), placeholder, cursor_on, background);
    field(app).area = input;
    crate::mouse::register_region(app, input, target);
    let chrome = (input.y, input.x, input.right().saturating_sub(1));
    app.view.bottom_app_selection_chrome.push(chrome);
}

/// One option line, clipped to the option column so wide glyphs never reach the border.
pub(crate) fn draw_line(
    f: &mut Frame,
    area: Rect,
    y: u16,
    highlighted: bool,
    spans: &[Span<'static>],
    overflows: bool,
) {
    let gutter = if overflows { SCROLLBAR_GUTTER } else { GUTTER };
    let bar = Rect::new(area.x + 3, y, area.width.saturating_sub(gutter), 1);
    if highlighted {
        list_cursor::paint(f, bar);
    }
    let mut x = bar.x;
    for span in spans {
        let room = bar.right().saturating_sub(x) as usize;
        x = f
            .buffer_mut()
            .set_stringn(x, y, &span.content, room, span.style)
            .0;
    }
}

/// `target`'s option list in `list`: registered for clicks, scrolled by its layout, with a scrollbar once it overflows.
pub(crate) fn draw_list(
    app: &mut App,
    f: &mut Frame,
    area: Rect,
    list: Rect,
    lines: &[VisualLine],
    target: MouseTarget,
) {
    crate::mouse::register_region(app, list, target);
    let options: Vec<_> = lines.iter().map(|line| line.option).collect();
    let view = crate::mouse::option_list(app, target).expect("a browser target has an option list");
    let offset = view.layout(&options, list);
    draw_lines(app, f, area, list, lines, offset, target);
}

/// `lines` from `offset` in `pane`, with `target`'s scrollbar once they overflow it.
pub(crate) fn draw_lines(
    app: &mut App,
    f: &mut Frame,
    area: Rect,
    pane: Rect,
    lines: &[VisualLine],
    offset: usize,
    target: MouseTarget,
) {
    let visible = usize::from(pane.height);
    let overflows = lines.len() > visible;
    for (row, line) in lines.iter().skip(offset).take(visible).enumerate() {
        let y = pane.y + row as u16;
        draw_line(f, area, y, line.highlighted, &line.spans, overflows);
    }
    if overflows {
        let bar = Rect::new(area.right().saturating_sub(4), pane.y, 1, pane.height);
        let total = lines.len() as u16;
        scrollbar::draw(app, f, target, bar, total, pane.height, offset as u16);
    }
}

#[cfg(test)]
mod tests;

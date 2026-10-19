//! The `/debug` console panel: a right-docked live log view (box, header,
//! scrollable log).

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Padding, Paragraph};
use ratatui::Frame;

use super::hint_line;
use super::scrollbar;
use super::theme;
use crate::app::App;
use crate::debug_console::{self, level_style, LogEntry};
use crate::hints::{action, key};
use crate::mouse::{self, MouseTarget};
use crate::utils::scroll::scroll_view;
use crate::utils::text::wrap_hard;

/// The stamp's cell width in a formatted log line.
const STAMP_WIDTH: usize = 19;
/// The level's padded cell width in a formatted log line.
const LEVEL_WIDTH: usize = 8;
/// The narrowest screen the panel docks on (the todo sidebar's rule): below
/// it the panel stays hidden, so the main chat area keeps at least the
/// panel's own 40-column minimum.
const MIN_DOCK_WIDTH: u16 = 80;
/// The shortest screen the panel docks on: below it the panel stays hidden,
/// so its navigation keys return to the composer while nothing renders.
const MIN_DOCK_HEIGHT: u16 = 3;

/// Split the screen into the session area and the console panel (docked right,
/// 40% wide within 40..=80 columns), with the todo sidebar's minimum-dock-width
/// rule so the composer stays usable.
pub fn split(app: &App, area: Rect) -> (Rect, Option<Rect>) {
    if !app.debug_console.open || area.width < MIN_DOCK_WIDTH || area.height < MIN_DOCK_HEIGHT {
        return (area, None);
    }
    let width = (area.width * 40 / 100).clamp(40, 80);
    let main = Rect {
        width: area.width - width,
        ..area
    };
    let panel = Rect {
        x: main.right(),
        width,
        ..area
    };
    (main, Some(panel))
}

/// Draw the console panel into its docked area.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    if area.width < 3 || area.height < 2 {
        return;
    }
    f.buffer_mut().set_style(area, theme::screen_style());
    // Solid left border in `$foreground-muted`, one column of padding each side.
    let block = Block::default()
        .borders(Borders::LEFT)
        .border_style(theme::text(theme::popup_border()))
        .padding(Padding::horizontal(1));
    let inner = block.inner(area);
    f.render_widget(block, area);
    if inner.width < 1 || inner.height < 2 {
        return;
    }
    let mut spans = vec![Span::styled(
        "Debug Console  ",
        theme::text(theme::foreground()),
    )];
    spans.extend(hint_line::spans(&[(key::CTRL_BACKSLASH, action::CLOSE)]));
    let header = Line::from(spans);
    f.render_widget(Paragraph::new(header), Rect { height: 1, ..inner });
    // One blank row below the header.
    let body = Rect {
        y: inner.y + 2,
        height: inner.height - 2,
        ..inner
    };
    draw_body(app, f, body);
}

/// Draw the scrollable log body, publishing its height, wrap counts, and hitmap.
fn draw_body(app: &mut App, f: &mut Frame, area: Rect) {
    // The body alone is scrollable and clickable; the header and border stay
    // inert.
    mouse::register_region(app, area, MouseTarget::DebugConsole);
    let (total, view) = {
        let console = &mut app.debug_console;
        // The displayed top line before this draw's mutations; the re-wrap
        // below keeps it.
        let anchor = debug_console::view(console).position;
        console.body_area = area;
        console.body_height = area.height;
        let rewrapped = console.wrap_width != area.width;
        if rewrapped {
            console.wrap_width = area.width;
            console.wrap_counts = console
                .entries
                .iter()
                .map(|entry| {
                    wrap_hard(&entry.plain_line(), area.width as usize)
                        .len()
                        .max(1)
                })
                .collect();
        }
        let total = debug_console::total_visual(console).min(u16::MAX as usize) as u16;
        if rewrapped {
            console.scroll = total.saturating_sub(area.height).saturating_sub(anchor);
        }
        let view = scroll_view(total, area.height, console.scroll);
        console.scroll = view.scroll; // keep the stored offset canonical
        let top = view.position as usize;
        let bottom = top + area.height as usize;
        let mut lines: Vec<Line> = Vec::new();
        let mut hitmap: Vec<(u16, u16, usize)> = Vec::new();
        let mut visual = 0usize;
        for (index, entry) in console.entries.iter().enumerate() {
            let start = visual;
            visual += console.wrap_counts[index];
            if visual <= top || start >= bottom {
                continue;
            }
            for (offset, text) in wrap_hard(&entry.plain_line(), area.width as usize)
                .iter()
                .enumerate()
            {
                let row = start + offset;
                if row < top || row >= bottom {
                    continue;
                }
                let screen_row = area.y + (row - top) as u16;
                lines.push(styled_line(entry, offset == 0, text));
                hitmap.push((screen_row, screen_row + 1, index));
            }
        }
        console.hitmap = hitmap;
        f.render_widget(Paragraph::new(lines), area);
        (total, view)
    };
    // The log paints a 1-column scrollbar over its right edge when it
    // overflows; dragging it never loads pages.
    if view.overflow {
        scrollbar::draw_large(
            app,
            f,
            MouseTarget::DebugConsole,
            Rect {
                x: area.right().saturating_sub(1),
                width: 1,
                ..area
            },
            total as usize,
            area.height as usize,
            view.position as usize,
        );
    }
}

/// One wrapped visual line of an entry: dim timestamp, colored level, message.
fn styled_line(entry: &LogEntry, first: bool, text: &str) -> Line<'static> {
    if !first {
        return Line::from(Span::raw(text.to_owned()));
    }
    let stamp_end = STAMP_WIDTH.min(text.len());
    let rest = &text[stamp_end..];
    // The prefix is fixed-width ASCII; a fold inside it (a very narrow panel)
    // renders unstyled rather than coloring a partial prefix.
    let Some((level_part, message)) = rest.split_at_checked(1 + entry.level.len().max(LEVEL_WIDTH))
    else {
        return Line::from(Span::raw(text.to_owned()));
    };
    Line::from(vec![
        Span::styled(text[..stamp_end].to_owned(), theme::muted_style()),
        Span::styled(level_part.to_owned(), level_style(&entry.level)),
        Span::raw(message.to_owned()),
    ])
}

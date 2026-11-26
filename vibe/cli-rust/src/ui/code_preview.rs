//! The code preview popup: a highlighted slice of the linked file beside the hovered link.

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{scrollbar, theme};
use crate::app::App;
use crate::code_preview::telemetry::Close;
use crate::code_preview::Snippet;
use crate::mouse::MouseTarget;
use crate::selection::{Region, RegionId};

const MAX_ROWS: u16 = 16;
const MIN_WIDTH: u16 = 24;

/// Forget the last frame's popup; a surface other than the transcript closes it.
pub fn reset(app: &mut App) {
    if !crate::code_preview::transcript_focused(app) {
        return crate::code_preview::clear(app, Close::FocusChanged);
    }
    let preview = &mut app.code_preview;
    preview.area = Rect::default();
    preview.selection = Region::default();
    preview.gutters.clear();
}

/// Paint the open preview across `bounds`, below the hovered link or above it when it fits better.
pub fn draw(app: &mut App, f: &mut Frame, bounds: Rect) {
    if !draw_open(app, f, bounds) {
        crate::selection::clear_region(app, RegionId::CodePreview);
    }
}

/// Paint the popup and publish its selectable code; false when nothing is open.
fn draw_open(app: &mut App, f: &mut Frame, bounds: Rect) -> bool {
    let Some(row) = anchor(app) else {
        return false;
    };
    let preview = &app.code_preview;
    let Some(snippet) = preview.snippet.as_ref().filter(|_| preview.shown) else {
        return false;
    };
    let digits = (snippet.first + snippet.lines.len() - 1).to_string().len();
    let width = bounds.width;
    let height = (snippet.lines.len() as u16).min(MAX_ROWS) + 2;
    if width < MIN_WIDTH {
        return false;
    }
    let Some(area) = place(bounds, row, width, height) else {
        return false;
    };
    let (scroll, max_scroll, inner) = paint(f, area, snippet, digits, preview.scroll);
    let (total, visible) = (snippet.lines.len(), inner.height as usize);
    let gutter = digits as u16 + 3;
    let preview = &mut app.code_preview;
    preview.scroll = scroll;
    preview.max_scroll = max_scroll;
    preview.area = area;
    preview.gutters = (inner.y..inner.bottom())
        .map(|y| (y, inner.x, inner.x + gutter - 1))
        .collect();
    preview.selection = Region {
        scrollbar: total > visible,
        ..super::selection::frame_region(inner)
    };
    crate::code_preview::telemetry::rendered(app);
    crate::mouse::register_region(app, area, MouseTarget::CodePreview);
    if total > visible {
        let bar = Rect::new(inner.right() - 1, inner.y, 1, inner.height);
        scrollbar::draw_large(
            app,
            f,
            MouseTarget::CodePreview,
            bar,
            total,
            visible,
            scroll,
        );
    }
    super::selection::overlay_region(app, f, RegionId::CodePreview);
    true
}

/// The row the pointer entered the hovered link at; a link scrolled away from its anchor closes the preview.
fn anchor(app: &mut App) -> Option<u16> {
    let (target, at) = app.code_preview.link.as_ref()?;
    app.view.mouse_position?;
    let row = app
        .view
        .link_hitmap
        .iter()
        .any(|link| link.target() == target && link.contains(*at))
        .then_some(at.1);
    if row.is_none() {
        crate::code_preview::clear(app, Close::LinkMoved);
    }
    row
}

/// Below `row` or above it, so a wrapped link's other rows never sit between the pointer and the popup.
fn place(bounds: Rect, row: u16, width: u16, height: u16) -> Option<Rect> {
    let below = bounds.bottom().saturating_sub(row + 1);
    let above = row.saturating_sub(bounds.y);
    let (y, height) = match below >= height || below >= above {
        true => (row + 1, height.min(below)),
        false => (row - height.min(above), height.min(above)),
    };
    if height < 3 {
        return None;
    }
    Some(Rect::new(bounds.x, y, width, height))
}

/// Draw the box, its numbered lines from `scroll` and the visible range; returns the clamped scroll, its maximum and the inner area.
fn paint(
    f: &mut Frame,
    area: Rect,
    snippet: &Snippet,
    digits: usize,
    scroll: usize,
) -> (usize, usize, Rect) {
    let surface = Style::default().bg(theme::background());
    f.render_widget(Clear, area);
    f.buffer_mut().set_style(area, surface);
    let title = Span::styled(
        format!(" {} ", snippet.title),
        surface.fg(theme::foreground()),
    );
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::border_style().bg(theme::background()))
        .title(Line::from(title));
    let visible = area.height.saturating_sub(2) as usize;
    let max_scroll = snippet.lines.len().saturating_sub(visible);
    let scroll = scroll.min(max_scroll);
    let first = snippet.first + scroll;
    let last = first + visible.min(snippet.lines.len() - scroll) - 1;
    let position = Span::styled(
        format!(" {first}-{last} of {} ", snippet.total),
        theme::muted_style().bg(theme::background()),
    );
    let block = block.title_bottom(Line::from(position).right_aligned());
    let inner = block.inner(area);
    f.render_widget(block, area);
    let code_width = inner.width - u16::from(snippet.lines.len() > visible);
    let buffer = f.buffer_mut();
    for (row, spans) in snippet.lines.iter().skip(scroll).take(visible).enumerate() {
        let number = first + row;
        let focused = snippet
            .focus
            .is_some_and(|(start, end)| (start..=end).contains(&number));
        let bg = match focused {
            true => theme::block_hover_bg(theme::background()),
            false => theme::background(),
        };
        let y = inner.y + row as u16;
        let base = Style::default().fg(theme::foreground()).bg(bg);
        buffer.set_style(Rect::new(inner.x, y, inner.width, 1), base);
        let gutter = Span::styled(format!(" {number:>digits$}  "), theme::muted_style().bg(bg));
        let line = Line::from(
            std::iter::once(gutter)
                .chain(spans.iter().cloned())
                .collect::<Vec<_>>(),
        );
        buffer.set_line(inner.x, y, &line, code_width);
    }
    (scroll, max_scroll, inner)
}

//! Scrollable rendering for pre-wrapped question-app rows.

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{scrollbar, theme};
use crate::app::App;

pub(super) type Row = Vec<(String, Style)>;

/// The prefix cells of every visible option row, as selection-chrome gaps.
pub(super) fn prefix_gaps(
    prefixes: &[(u16, u16)],
    content: Rect,
    scroll: u16,
    visible: u16,
) -> Vec<crate::selection::RowSpan> {
    let mut gaps = Vec::new();
    for &(row, width) in prefixes {
        if row < scroll || row >= scroll + visible {
            continue;
        }
        let x1 = content.x.saturating_add(width).saturating_sub(1);
        gaps.push((content.y + row - scroll, content.x, x1));
    }
    gaps
}

pub(super) fn draw_box(
    app: &mut App,
    f: &mut Frame,
    area: Rect,
    rows: &[Row],
    scroll: u16,
    total: u16,
) {
    if area.height < 3 {
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

    let x = area.x + 2;
    let width = area.width.saturating_sub(4) as usize;
    let visible = area.height.saturating_sub(2);
    for (screen_row, row) in rows
        .iter()
        .skip(scroll as usize)
        .take(visible as usize)
        .enumerate()
    {
        let mut cx = x;
        for (text, style) in row {
            let room = width.saturating_sub((cx - x) as usize);
            let style = match style.bg {
                Some(_) => *style,
                None => style.bg(theme::background()),
            };
            f.buffer_mut()
                .set_stringn(cx, area.y + 1 + screen_row as u16, text, room, style);
            cx += text.chars().count().min(room) as u16;
        }
    }
    if total > visible {
        let bar = Rect::new(area.right().saturating_sub(2), area.y + 1, 1, visible);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::Question,
            bar,
            total,
            visible,
            scroll,
        );
    }
}

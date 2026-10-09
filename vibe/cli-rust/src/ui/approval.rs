//! Tool-approval bottom app.

mod detail;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, Paragraph};
use ratatui::Frame;

use super::{list_cursor, scrollbar, theme};
use crate::app::App;
use crate::hints::{self, action, key};
use crate::utils::text::wrap_hard;

const MAX_HEIGHT_RATIO: (u16, u16) = (7, 10);
const OPTIONS: [&str; 4] = [
    "Allow once",
    "Allow for remainder of this session",
    "Always allow",
    "Deny",
];

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let max_height = (area.height * MAX_HEIGHT_RATIO.0 / MAX_HEIGHT_RATIO.1).max(2);
    let rows = rows(
        app,
        area.width.saturating_sub(4),
        max_height.saturating_sub(2),
    );
    let box_height = (rows.lines.len() as u16 + 2).min(max_height);
    super::bottom_app::draw(
        app,
        f,
        area,
        box_height,
        super::bottom_app::Kind::Approval,
        |app, f, area| draw_box(app, f, area, rows),
    );
}

struct ApprovalRows {
    lines: Vec<Line<'static>>,
    detail_start: u16,
    detail_total: usize,
    detail_viewport: usize,
    detail_scroll: usize,
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, rows: ApprovalRows) {
    if area.height < 3 {
        return;
    }
    f.render_widget(Clear, area);
    let background = theme::background();
    f.buffer_mut()
        .set_style(area, Style::default().bg(background));
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(theme::popup_border()).bg(background));
    let inner = block.inner(area);
    f.render_widget(block, area);
    let content = Rect {
        x: inner.x.saturating_add(1),
        width: inner.width.saturating_sub(2),
        ..inner
    };
    f.render_widget(
        Paragraph::new(rows.lines).style(Style::default().bg(background)),
        content,
    );
    if rows.detail_total > rows.detail_viewport && rows.detail_viewport > 0 {
        let scroll_area = Rect {
            x: content.right().saturating_sub(1),
            y: content.y.saturating_add(rows.detail_start),
            width: 1,
            height: rows.detail_viewport as u16,
        };
        scrollbar::draw_large(
            app,
            f,
            crate::mouse::MouseTarget::Approval,
            scroll_area,
            rows.detail_total,
            rows.detail_viewport,
            rows.detail_scroll,
        );
    }
}

fn rows(app: &mut App, width: u16, max_rows: u16) -> ApprovalRows {
    let Some(callback) = app.approval.active.as_ref() else {
        return ApprovalRows {
            lines: Vec::new(),
            detail_start: 0,
            detail_total: 0,
            detail_viewport: 0,
            detail_scroll: 0,
        };
    };
    let labels = callback
        .detail
        .required_permissions
        .iter()
        .filter_map(|permission| {
            (!permission.label.is_empty()).then_some(permission.label.as_str())
        })
        .collect::<Vec<_>>()
        .join(", ");
    let title = if labels.is_empty() {
        format!(
            "Permission for the {} tool",
            callback.detail.effect.tool_name
        )
    } else {
        format!(
            "Permission for the {} tool ({labels})",
            callback.detail.effect.tool_name
        )
    };
    let title_style = theme::text(theme::warning()).add_modifier(Modifier::BOLD);
    let reserved = OPTIONS.len() + 3;
    let title_limit = (max_rows as usize).saturating_sub(reserved);
    let title =
        crate::utils::text::ellipsize(&title, usize::from(width).saturating_mul(title_limit));
    let mut title_rows = wrap_hard(&title, width as usize)
        .into_iter()
        .map(|text| Line::from(Span::styled(text, title_style)))
        .collect::<Vec<_>>();
    title_rows.truncate(title_limit);
    let detail_capacity = (max_rows as usize).saturating_sub(title_rows.len() + reserved);
    let same_detail = app.approval.detail_callback_id.as_deref()
        == Some(callback.callback_id.as_str())
        && app.approval.detail_width == width;
    if !same_detail {
        app.approval.detail_scroll = 0;
    }
    let previous_scrollbar = same_detail
        && app.approval.detail_rows > app.approval.detail_viewport
        && app.approval.detail_viewport > 0;
    let known_total = same_detail.then_some(app.approval.detail_rows);
    let max_scroll = known_total.unwrap_or(0).saturating_sub(detail_capacity);
    let mut detail_scroll = app.approval.detail_scroll.min(max_scroll);
    let detail_width = width.saturating_sub(u16::from(previous_scrollbar));
    let mut detail_rows = detail::window(
        &callback.detail.effect,
        app.approval.detail_preview.as_ref(),
        detail_width,
        detail_scroll,
        detail_capacity,
        known_total,
    );
    let scrollbar = detail_capacity > 0 && detail_rows.total > detail_capacity;
    if scrollbar != previous_scrollbar {
        detail_scroll = 0;
        detail_rows = detail::window(
            &callback.detail.effect,
            app.approval.detail_preview.as_ref(),
            width.saturating_sub(u16::from(scrollbar)),
            detail_scroll,
            detail_capacity,
            None,
        );
    }
    let detail_viewport = detail_capacity.min(detail_rows.total);
    app.approval.detail_scroll = detail_scroll;
    app.approval.detail_rows = detail_rows.total;
    app.approval.detail_viewport = detail_viewport;
    app.approval.detail_callback_id = Some(callback.callback_id.clone());
    app.approval.detail_width = width;
    let detail_start = title_rows.len() as u16;
    title_rows.extend(detail_rows.lines);
    title_rows.push(Line::default());
    title_rows.extend(option_rows(app.approval.selected, width));
    title_rows.push(Line::default());
    title_rows.push(help());
    ApprovalRows {
        lines: title_rows,
        detail_start,
        detail_total: app.approval.detail_rows,
        detail_viewport,
        detail_scroll,
    }
}

fn option_rows(selected: usize, width: u16) -> impl Iterator<Item = Line<'static>> {
    OPTIONS.into_iter().enumerate().map(move |(index, label)| {
        let text = format!("  {}. {label}", index + 1);
        if index == selected {
            let bar = list_cursor::padded(&text, usize::from(width));
            return Line::from(Span::styled(bar, list_cursor::style()));
        }
        let color = if index == OPTIONS.len() - 1 {
            theme::error()
        } else {
            theme::foreground()
        };
        Line::from(Span::styled(text, theme::text(color)))
    })
}

fn help() -> Line<'static> {
    super::hint_line::line(&[hints::NAVIGATE, hints::SELECT, (key::ESC, action::REJECT)])
}

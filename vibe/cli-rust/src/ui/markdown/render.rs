//! Styled blocks to padded, wrapped ratatui lines.

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::text::{cell_width, pad, pad_line, wrap_chars};
use super::{Block, Frame, Item, Out, Profile};
use crate::ui::{styled_text, theme};

pub(super) fn render_block(b: &Block, width: u16, frame: Frame, out: &mut Out) {
    match b {
        Block::Heading(1, inline) => {
            let inner = (width as usize).saturating_sub(2 * frame.pad);
            if frame.center_h1 {
                for row in wrap_chars(inline, inner) {
                    let start = frame.pad + inner.saturating_sub(cell_width(&row)) / 2;
                    out.lines.push_row(vec![pad(start)], &row);
                }
            } else {
                for row in wrap_chars(inline, inner) {
                    out.lines.push_row(vec![pad(frame.pad)], &row);
                }
            }
        }
        Block::Heading(_, inline) => {
            for row in wrap_chars(inline, (width as usize).saturating_sub(2 * frame.pad)) {
                out.lines.push_row(vec![pad(frame.pad)], &row);
            }
        }
        Block::Paragraph(inline) => {
            let body = (width as usize).saturating_sub(2 * frame.pad);
            for row in wrap_chars(inline, body) {
                out.lines.push_row(vec![pad(frame.pad)], &row);
            }
        }
        Block::Rule => {
            let inner = (width as usize).saturating_sub(2 * frame.pad);
            let rule = |out: &mut Out| {
                out.lines.push(Line::from(vec![
                    pad(frame.pad),
                    Span::styled("─".repeat(inner), Style::default().fg(theme::md_rule())),
                ]));
            };
            // The widget's `padding-top: 1` renders inside the block.
            if frame.profile == Profile::Widget {
                out.lines.push(Line::from(""));
            }
            rule(out);
        }
        Block::Code(lines) => render_code(lines, width, frame, out),
        Block::List { start, items } => render_list(*start, items, (0, 0), width, frame, out),
        Block::Table { headers, rows } => {
            super::table::render_table(headers, rows, width, frame.pad, out);
            out.table += 1;
        }
        Block::Quote(inline) => {
            let inner = (width as usize).saturating_sub(2 * frame.pad + 2);
            let bar = match frame.profile {
                Profile::Assistant => theme::md_quote_bar(),
                Profile::Widget => theme::md_widget_quote_bar(),
            };
            for row in wrap_chars(inline, inner) {
                out.lines.push_row(
                    vec![
                        pad(frame.pad),
                        Span::styled("▌", Style::default().fg(bar)),
                        Span::raw(" "),
                    ],
                    &row,
                );
            }
        }
    }
}

/// A fence body. A standalone widget keeps the `MarkdownFence > Label`
/// padding (one blank row above and below, two indent columns on truecolor)
/// and washes the label box in the fence background; the chat tcss zeroes it.
fn render_code(lines: &[Vec<Span<'static>>], width: u16, frame: Frame, out: &mut Out) {
    let content = (width as usize).saturating_sub(2 * frame.pad);
    let (wrap, indent, bg) = match frame.profile {
        Profile::Assistant => (content, 0, None),
        Profile::Widget => {
            let label = if theme::is_ansi() { 0 } else { 2 };
            (
                content.saturating_sub(2 * label),
                label,
                theme::md_widget_fence_bg(),
            )
        }
    };
    let widget = frame.profile == Profile::Widget;
    let wash = |spans: &mut Vec<Span<'static>>, fill: usize| {
        spans.push(Span::styled(" ".repeat(fill), bg_style(bg)));
    };
    if widget {
        let mut row = Vec::new();
        wash(&mut row, content);
        out.lines.push(pad_line(row, frame.pad));
    }
    for line in lines {
        if line.iter().all(|span| span.content.trim().is_empty()) {
            out.lines.push(Line::default());
            continue;
        }
        for row in styled_text::wrap_hard(line, wrap) {
            let mut spans = Vec::new();
            if indent > 0 {
                wash(&mut spans, indent);
            }
            let mut used = indent;
            for mut span in row {
                if let Some(bg) = bg {
                    span.style = span.style.bg(bg);
                }
                used += span.content.width();
                spans.push(span);
            }
            if widget {
                wash(&mut spans, content.saturating_sub(used));
            }
            out.lines.push(pad_line(spans, frame.pad));
        }
    }
    if widget {
        let mut row = Vec::new();
        wash(&mut row, content);
        out.lines.push(pad_line(row, frame.pad));
    }
}

fn bg_style(bg: Option<ratatui::style::Color>) -> Style {
    match bg {
        Some(bg) => Style::default().bg(bg),
        None => Style::default(),
    }
}

/// Render a list at nesting `depth`, its markers offset by `indent` cells.
fn render_list(
    start: Option<u64>,
    items: &[Item],
    nesting: (usize, usize),
    width: u16,
    frame: Frame,
    out: &mut Out,
) {
    let (depth, indent) = nesting;
    let marker_color = match frame.profile {
        Profile::Assistant => theme::foreground(),
        Profile::Widget => theme::md_widget_marker(),
    };
    let fg = Style::default().fg(marker_color);
    for (marker, item) in markers(start, items.len(), depth).iter().zip(items) {
        let marker_len = marker.width();
        let avail = (width as usize).saturating_sub(2 * frame.pad + indent + marker_len);
        for (i, row) in wrap_chars(&item.inline, avail).iter().enumerate() {
            let marker = match i {
                0 => Span::styled(marker.clone(), fg),
                _ => pad(marker_len),
            };
            out.lines
                .push_row(vec![pad(frame.pad), pad(indent), marker], row);
        }
        let child_indent = indent + marker_len;
        for child in &item.children {
            match child {
                Block::List { start, items } => {
                    render_list(*start, items, (depth + 1, child_indent), width, frame, out)
                }
                other => render_block(other, width, frame, out),
            }
        }
    }
}

/// The per-item marker column: bullets cycle by depth, ordered items are `n. `.
fn markers(start: Option<u64>, count: usize, depth: usize) -> Vec<String> {
    const BULLETS: [&str; 5] = ["• ", "▪ ", "‣ ", "⭑ ", "◦ "];
    let Some(start) = start else {
        return vec![BULLETS[depth % BULLETS.len()].to_string(); count];
    };
    let nums: Vec<u64> = (0..count as u64).map(|i| start + i).collect();
    let symbol_size = nums
        .iter()
        .map(|n| format!("{n}. ").len())
        .max()
        .unwrap_or(0);
    nums.iter()
        .map(|n| format!("{:>width$}", format!("{n}. "), width = symbol_size + 1))
        .collect()
}

//! Styled blocks to padded, wrapped ratatui lines.

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::text::{pad_line, wrap};
use super::{Block, Frame, Item, Out};
use crate::ui::{styled_text, theme};

pub(super) fn render_block(b: &Block, width: u16, frame: Frame, out: &mut Out) {
    match b {
        Block::Heading(1, inline) => {
            let inner = (width as usize).saturating_sub(2 * frame.pad);
            if frame.center_h1 {
                for row in wrap(inline, inner) {
                    let row_width = styled_text::width(&row);
                    let start = frame.pad + inner.saturating_sub(row_width) / 2;
                    let mut spans = vec![Span::raw(" ".repeat(start))];
                    spans.extend(row);
                    out.lines.push(Line::from(spans));
                }
            } else {
                for row in wrap(inline, inner) {
                    out.lines.push(pad_line(row, frame.pad));
                }
            }
        }
        Block::Heading(_, inline) => {
            for row in wrap(inline, (width as usize).saturating_sub(2 * frame.pad)) {
                out.lines.push(pad_line(row, frame.pad));
            }
        }
        Block::Paragraph(inline) => {
            let body = (width as usize).saturating_sub(2 * frame.pad);
            for row in wrap(inline, body) {
                out.lines.push(pad_line(row, frame.pad));
            }
        }
        Block::Code(lines) => {
            let inner = (width as usize).saturating_sub(2 * frame.pad);
            for line in lines {
                if line.iter().all(|span| span.content.trim().is_empty()) {
                    out.lines.push(Line::default());
                    continue;
                }
                for row in styled_text::wrap_hard(line, inner) {
                    out.lines.push(pad_line(row, frame.pad));
                }
            }
        }
        Block::List { start, items } => render_list(*start, items, (0, 0), width, frame, out),
        Block::Table { headers, rows } => {
            super::table::render_table(headers, rows, width, frame.pad, out);
            out.table += 1;
        }
        Block::Quote(inline) => {
            let inner = (width as usize).saturating_sub(2 * frame.pad + 2);
            for row in wrap(inline, inner) {
                let mut spans = vec![
                    Span::styled("▌", Style::default().fg(theme::md_quote_bar())),
                    Span::raw(" "),
                ];
                spans.extend(row);
                out.lines.push(pad_line(spans, frame.pad));
            }
        }
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
    let fg = Style::default().fg(theme::foreground());
    for (marker, item) in markers(start, items.len(), depth).iter().zip(items) {
        let marker_len = marker.width();
        let avail = (width as usize).saturating_sub(2 * frame.pad + indent + marker_len);
        let rows = wrap(&item.inline, avail);
        for (i, row) in rows.into_iter().enumerate() {
            let mut spans = vec![Span::raw(" ".repeat(indent))];
            if i == 0 {
                spans.push(Span::styled(marker.clone(), fg));
            } else {
                spans.push(Span::raw(" ".repeat(marker_len)));
            }
            spans.extend(row);
            out.lines.push(pad_line(spans, frame.pad));
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

//! Diff rows word-wrapped to a width, continuation rows hung under the gutter.

use ratatui::style::{Color, Style};
use ratatui::text::Span;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use super::DiffRow;
use crate::ui::styled_text;

/// Wrap `row`'s code to `width` cells, with blank gutter-wide hangs and fold gaps.
pub fn wrap_row(row: &DiffRow, width: u16) -> Vec<(Vec<Span<'static>>, Option<u16>)> {
    let gutter_width = row.gutter_width as usize;
    let (gutter, body) = match gutter_width {
        0 => (Vec::new(), row.spans.clone()),
        _ => styled_text::split_at_width(&row.spans, gutter_width),
    };
    let body_width = (width as usize).saturating_sub(gutter_width).max(1);
    styled_text::wrap_hard_folded(&body, body_width)
        .into_iter()
        .enumerate()
        .map(|(index, (body, gap))| {
            let mut spans = match index {
                0 => gutter.clone(),
                _ => vec![Span::raw(" ".repeat(gutter_width))],
            };
            spans.extend(body);
            (spans, gap)
        })
        .collect()
}

/// Clip a row to the diff view's width and paint its band across the whole of it.
pub fn banded(spans: &[Span<'static>], band: Option<Color>, width: u16) -> Vec<Span<'static>> {
    let mut out = Vec::with_capacity(spans.len() + 1);
    let mut used = 0usize;
    for span in spans {
        let room = (width as usize).saturating_sub(used);
        if room == 0 {
            break;
        }
        let content = truncate(&span.content, room);
        used += content.width();
        let style = match band {
            Some(color) => span.style.bg(color),
            None => span.style,
        };
        out.push(Span::styled(content, style));
    }
    if let Some(color) = band {
        let padding = (width as usize).saturating_sub(used);
        if padding > 0 {
            out.push(Span::styled(
                " ".repeat(padding),
                Style::default().bg(color),
            ));
        }
    }
    out
}

/// Cut to `width` cells, so a wide glyph never straddles the band's edge.
fn truncate(text: &str, width: usize) -> String {
    if text.width() <= width {
        return text.to_owned();
    }
    let mut out = String::new();
    let mut used = 0;
    for c in text.chars() {
        let cell = c.width().unwrap_or(0);
        if used + cell > width {
            break;
        }
        out.push(c);
        used += cell;
    }
    out
}

#[cfg(test)]
mod tests;

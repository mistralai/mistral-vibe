//! Hint rendering: keys in bold $primary, actions in $text-muted, laid out by `hints::runs` and wrapped by `hints::rows`, never cut.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::theme;
use crate::hints::{self, action, key, Hint};

/// The `/thinking` picker's hints.
pub(crate) const PICK_HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER, action::SAVE),
    ("s", action::SESSION_ONLY),
    hints::CANCEL,
];

pub(crate) fn key_style() -> Style {
    theme::text(theme::primary()).add_modifier(Modifier::BOLD)
}

pub(crate) fn action_style() -> Style {
    theme::muted_style()
}

/// The hint row as styled `(text, style)` runs.
pub(crate) fn styled(hints: &[Hint]) -> Vec<(String, Style)> {
    hints::runs(hints)
        .into_iter()
        .map(|(text, is_key)| {
            let style = if is_key { key_style() } else { action_style() };
            (text.to_owned(), style)
        })
        .collect()
}

pub(crate) fn spans(hints: &[Hint]) -> Vec<Span<'static>> {
    styled(hints)
        .into_iter()
        .map(|(text, style)| Span::styled(text, style))
        .collect()
}

pub(crate) fn line(hints: &[Hint]) -> Line<'static> {
    Line::from(spans(hints))
}

/// Inline status hints: `key to action` pairs joined by ` · `, for loading-like lines.
pub(crate) fn inline_spans(hints: &[Hint]) -> Vec<Span<'static>> {
    let mut spans = Vec::with_capacity(hints.len() * 3);
    for (index, &(keys, action)) in hints.iter().enumerate() {
        if index > 0 {
            spans.push(Span::styled(" · ", action_style()));
        }
        spans.push(Span::styled(keys, key_style()));
        spans.push(Span::styled(format!(" to {action}"), action_style()));
    }
    spans
}

/// The hints wrapped to `width` columns, one line per row.
pub(crate) fn lines(hints: &[Hint], width: u16) -> Vec<Line<'static>> {
    hints::rows(hints, width)
        .iter()
        .map(|row| line(row))
        .collect()
}

/// The hints wrapped to `width` columns, one row of styled runs per line.
pub(crate) fn styled_rows(hints: &[Hint], width: u16) -> Vec<Vec<(String, Style)>> {
    hints::rows(hints, width)
        .iter()
        .map(|row| styled(row))
        .collect()
}

/// The rows the hints take once wrapped to `width` columns.
pub(crate) fn height(hints: &[Hint], width: u16) -> u16 {
    hints::rows(hints, width).len() as u16
}

/// The rows a box footer adds beyond its one reserved row, once wrapped inside a box of `box_width` (border and padding off).
pub(crate) fn extra(hints: &[Hint], box_width: u16) -> u16 {
    height(hints, box_width.saturating_sub(4)).saturating_sub(1)
}

/// A box footer: the hints wrapped inside the box border and padding, their last row on the box's last inner row.
pub(crate) fn draw_footer(f: &mut Frame, area: Rect, hints: &[Hint]) {
    let bottom = area.bottom().saturating_sub(2);
    draw_above(f, area.x + 2, bottom, area.width.saturating_sub(4), hints);
}

/// Draw the hints wrapped to `area.width`, from `area.y` down.
pub(crate) fn draw(f: &mut Frame, area: Rect, hints: &[Hint]) {
    for (y, line) in (area.y..area.bottom()).zip(lines(hints, area.width)) {
        f.buffer_mut().set_line(area.x, y, &line, area.width);
    }
}

/// Draw the hints wrapped to `width` so their last row lands on `bottom`, the way a footer grows upward.
pub(crate) fn draw_above(f: &mut Frame, x: u16, bottom: u16, width: u16, hints: &[Hint]) {
    let top = (bottom + 1).saturating_sub(height(hints, width));
    draw(f, Rect::new(x, top, width, bottom + 1 - top), hints);
}

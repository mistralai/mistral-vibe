//! Hint rendering: keys in bold $primary, actions in $text-muted, laid out by `hints::runs`.

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::theme;
use crate::hints::{self, action, key, Hint};

/// Hints shared by the `/model` and `/thinking` pickers.
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

/// Draw a hint row at `(x, y)`, clipped two columns before the frame's right edge (a box border and padding).
pub(crate) fn draw(f: &mut Frame, x: u16, y: u16, hints: &[Hint]) {
    let right = f.area().right().saturating_sub(2);
    draw_clipped(f, x, y, right.saturating_sub(x), hints);
}

/// Draw a hint row at `(x, y)`, clipped to `width` columns.
pub(crate) fn draw_clipped(f: &mut Frame, x: u16, y: u16, width: u16, hints: &[Hint]) {
    f.buffer_mut().set_line(x, y, &line(hints), width);
}

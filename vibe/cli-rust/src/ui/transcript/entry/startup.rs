//! Client-owned startup banners (Python `messages.py` warning widgets).

use ratatui::style::Color;
use ratatui::text::{Line, Span};

use crate::ui::{markdown, theme};
use crate::utils::text;

/// Python `WarningMessage` with `show_border=False`: a plain `NoMarkupStatic`
/// whose text wraps in `$warning`, mounted straight into the messages area.
pub(super) fn push_warning(lines: &mut Vec<Line<'static>>, text: &str, width: u16) {
    let style = theme::text(theme::warning());
    // `.warning-content` keeps a two-cell right padding free.
    let body_width = width.saturating_sub(2) as usize;
    for row in text::wrap_hard(text, body_width) {
        lines.push(Line::from(Span::styled(row, style)));
    }
}

/// Python `WhatsNewMessage`: the markdown body under a heavy `$mistral_orange`
/// left border, one blank above it when history widgets existed at mount time.
pub(super) fn push_whats_new(
    lines: &mut Vec<Line<'static>>,
    text: &str,
    width: u16,
    after_history: bool,
) {
    if after_history {
        lines.push(Line::from(""));
    }
    push_guttered(lines, text, width, theme::ORANGE);
}

/// A markdown banner under a heavy left border: the promo (`$mistral_orange`),
/// the what's-new body, and the custom-tools deprecation (`$warning`).
pub(super) fn push_guttered(lines: &mut Vec<Line<'static>>, text: &str, width: u16, color: Color) {
    lines.extend(markdown::guttered(text, width, color));
}

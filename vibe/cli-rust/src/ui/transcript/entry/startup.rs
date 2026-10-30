//! Client-owned startup banners.

use ratatui::text::{Line, Span};

use crate::selection::{fold, Fold};
use crate::ui::markdown::{self, LinkedLines};
use crate::ui::theme;

/// A borderless warning: plain text that wraps in `$warning`, mounted straight
/// into the messages area.
pub(super) fn push_warning(lines: &mut LinkedLines, text: &str, width: u16) {
    let style = theme::text(theme::warning());
    // `.warning-content` keeps a two-cell right padding free.
    let body_width = width.saturating_sub(2) as usize;
    for (row, gap) in fold::wrap_hard(text, body_width) {
        lines.push_folded(Line::from(Span::styled(row, style)), Fold::hung(gap, 0));
    }
}

/// The release notes of a fresh session, in a titled frame, versions in `$primary`.
pub(super) fn push_whats_new(lines: &mut LinkedLines, text: &str, width: u16) {
    let is_version = |line: &Line| crate::whats_new::is_version_heading(line.to_string().trim());
    lines.append(markdown::framed(
        text,
        width,
        crate::whats_new::TITLE,
        is_version,
    ));
}

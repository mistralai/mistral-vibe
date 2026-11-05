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

/// The warnings collected before the first turn, in a `$warning`-framed box.
pub(super) fn push_startup_notices(lines: &mut LinkedLines, text: &str, width: u16) {
    let title = crate::notices::TITLE;
    lines.append(markdown::framed(
        text,
        width,
        title,
        theme::warning(),
        |_| false,
    ));
}

/// A server warning or error after the first turn: a `⚠`/`✗` row in its severity color.
pub(super) fn push_notice_row(lines: &mut LinkedLines, text: &str, width: u16, error: bool) {
    let (icon, color) = match error {
        true => ("✗", theme::error()),
        false => ("⚠", theme::warning()),
    };
    let style = theme::text(color);
    lines.push_gap();
    let body_width = width.saturating_sub(4) as usize;
    for (index, (row, gap)) in fold::wrap_hard(text, body_width).into_iter().enumerate() {
        let lead = if index == 0 { icon } else { " " };
        let line = Line::from(vec![
            Span::styled(format!("{lead} "), style),
            Span::styled(row, style),
        ]);
        lines.push_folded(line, Fold::hung(gap, 2));
    }
}

/// The release notes of a fresh session, in a titled frame, versions in `$primary`.
pub(super) fn push_whats_new(lines: &mut LinkedLines, text: &str, width: u16) {
    let is_version = |line: &Line| crate::whats_new::is_version_heading(line.to_string().trim());
    let title = crate::whats_new::TITLE;
    let border = theme::md_fence_border();
    lines.append(markdown::framed(text, width, title, border, is_version));
}

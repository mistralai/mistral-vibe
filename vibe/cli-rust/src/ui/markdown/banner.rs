//! Startup banners: the promo under a heavy gutter, the release notes in a titled box.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::{is_blank_line, prepare, LinkedLines, BANNER};
use crate::ui::theme;

/// A markdown body under a heavy `color` gutter (the VS Code extension promo).
pub fn guttered(text: &str, width: u16, color: Color) -> LinkedLines {
    let mut body = body(text, width, true);
    body.prefix(|_| Span::styled("┃ ", Style::default().fg(color)));
    body
}

/// A markdown body in the fenced-code box titled `title`; `accent` lines are `$primary`.
pub fn framed(text: &str, width: u16, title: &str, accent: impl Fn(&Line) -> bool) -> LinkedLines {
    let inner = width.saturating_sub(4) as usize;
    let border = Style::default().fg(theme::md_fence_border());
    let mut body = body(text, inner as u16 + 2, false);
    for line in body.lines_mut().iter_mut().filter(|line| accent(line)) {
        let primary = |span: &mut Span| span.style = span.style.fg(theme::primary());
        line.spans.iter_mut().for_each(primary);
    }
    body.prefix(|_| Span::styled("│ ", border));
    for line in body.lines_mut() {
        let pad = (inner + 4).saturating_sub(line.width() + 2);
        line.spans
            .push(Span::styled(format!("{} │", " ".repeat(pad)), border));
    }
    let label = crate::utils::text::ellipsize(&format!(" {title} "), inner);
    let rule = (inner + 1).saturating_sub(label.width());
    body.insert(
        0,
        Line::from(vec![
            Span::styled("┌─", border),
            Span::styled(label, border.add_modifier(Modifier::BOLD)),
            Span::styled(format!("{}┐", "─".repeat(rule)), border),
        ]),
    );
    body.push(Line::from(Span::styled(
        format!("└{}┘", "─".repeat(inner + 2)),
        border,
    )));
    body
}

/// The body two columns narrower, trimmed; `tight` also drops the blank row under the first block.
fn body(text: &str, width: u16, tight: bool) -> LinkedLines {
    let mut body = prepare(text, width.saturating_sub(2), BANNER, false).lines;
    let lines = body.lines();
    let first = lines
        .iter()
        .position(|line| !is_blank_line(line))
        .unwrap_or(lines.len());
    let end = lines
        .iter()
        .rposition(|line| !is_blank_line(line))
        .map_or(first, |last| last + 1);
    let gap = (tight && lines.get(first + 1).is_some_and(is_blank_line)).then_some(first + 1);
    body.retain(|index, _| index >= first && index < end && Some(index) != gap);
    body
}

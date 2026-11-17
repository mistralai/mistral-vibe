//! Notice entry rendering: side-channel signals that act on detail kind.

use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::bordered::inset_prefix;
use crate::goal::format::{self, NoticeTone};
use crate::selection::{fold, Fold};
use crate::server::{NoticeDetail, NoticeEntry};
use crate::ui::markdown::LinkedLines;
use crate::ui::theme;

/// Notices are side-channel signals, not transcript content: each detail kind
/// is acted on and renders nothing, except a completed hook with output which
/// mounts a hook message line inside the run's container, and a goal notice,
/// which paints one line where the goal changed or worked. A fired scheduled
/// loop annotates its user prompt instead. Client-owned notices (app-server
/// crash, failed suspend) render their message directly.
pub(super) fn push_notice(
    lines: &mut LinkedLines,
    notice: &NoticeEntry,
    width: u16,
    local: bool,
    grouped: bool,
) {
    if let Some(detail) = notice.detail.as_ref() {
        match detail.kind.as_deref() {
            Some("hook_completed") => return push_hook_message(lines, detail, width, grouped),
            Some("goal") if !local => return push_goal(lines, detail, &notice.message, width),
            _ => {}
        }
    }
    // Only client-owned notices (app-server crash, failed suspend) render their
    // message. Server notices mount nothing unless handled above (e.g.
    // `session_title_updated` only updates the title).
    if !local {
        return;
    }
    let color = match notice.level.as_str() {
        "error" => theme::error(),
        _ => theme::warning(),
    };
    lines.push(Line::from(vec![
        inset_prefix(theme::text(color)),
        Span::styled(notice.message.to_string(), theme::text(color)),
    ]));
}

/// A goal notice: glyph and label in the tone's color, then an objective (muted)
/// or counters (dim foreground), ellipsized to the width.
fn push_goal(lines: &mut LinkedLines, detail: &NoticeDetail, message: &str, width: u16) {
    let (glyph, tone, parts) = format::notice_parts(detail, message);
    let color = match tone {
        NoticeTone::Accent => theme::primary(),
        NoticeTone::Success => theme::success(),
        NoticeTone::Warning => theme::warning(),
    };
    let objective = detail.objective.as_deref();
    let mut pieces = vec![(format!("{glyph} "), theme::text(color))];
    for (index, part) in parts.iter().enumerate() {
        let style = if Some(part.as_str()) == objective {
            theme::muted_style()
        } else {
            theme::dim(theme::foreground())
        };
        let part = crate::utils::text::single_line(part);
        if index == 0 {
            pieces.push((part, theme::text(color)));
            continue;
        }
        pieces.push((" · ".to_owned(), theme::muted_style()));
        pieces.push((part, style));
    }
    let mut room = usize::from(width);
    let mut spans = Vec::new();
    for (text, style) in pieces {
        if room == 0 {
            break;
        }
        let text = crate::utils::text::ellipsize(&text, room);
        room = room.saturating_sub(text.width());
        spans.push(Span::styled(text, style));
    }
    lines.push_gap();
    lines.push(Line::from(spans));
}

/// A hook message line: `{severity icon} [{hook_name}] {content}` — the
/// icon colored by `status` (default warning), the text muted and dim. Visibility
/// is gated by `is_hidden_hook_notice`, so a notice reaching here has both its
/// hook name and content set inside an open container.
fn push_hook_message(lines: &mut LinkedLines, detail: &NoticeDetail, width: u16, grouped: bool) {
    let (Some(hook_name), Some(content)) = (detail.hook_name.as_deref(), detail.content.as_deref())
    else {
        return;
    };
    // A hook notice is a tool-group member: the line opening the group carries
    // the group's gap (a pre-tool line renders above its tool row).
    if !grouped {
        lines.push_gap();
    }
    let (icon, color) = match detail.status.as_deref() {
        Some("ok") => ("✓", theme::success()),
        Some("error") => ("✗", theme::error()),
        _ => ("⚠", theme::warning()),
    };
    // The pre-tool container gives the line no margin of its own.
    let body = format!("[{hook_name}] {content}");
    let body_width = width.saturating_sub(2) as usize;
    // `.hook-system-content`: `max-height: 3; overflow: hidden`, wrapping inside
    // the icon column's two cells.
    let rows: Vec<_> = fold::wrap_hard(&body, body_width)
        .into_iter()
        .take(3)
        .collect();
    let style = theme::muted_style();
    for (index, (row, gap)) in rows.into_iter().enumerate() {
        let icon_span = if index == 0 {
            Span::styled(icon, theme::text(color))
        } else {
            Span::raw("  ")
        };
        let line = Line::from(vec![icon_span, Span::raw(" "), Span::styled(row, style)]);
        lines.push_folded(line, Fold::hung(gap, 3));
    }
}

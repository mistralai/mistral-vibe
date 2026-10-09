//! Notice entry rendering: side-channel signals that act on detail kind.

use ratatui::text::{Line, Span};

use super::bordered::inset_prefix;
use crate::selection::{fold, Fold};
use crate::server::{NoticeDetail, NoticeEntry};
use crate::ui::markdown::LinkedLines;
use crate::ui::theme;

/// Notices are side-channel signals, not transcript content: Python acts on each
/// detail kind and renders nothing, except a completed hook with output which
/// mounts a `HookSystemMessageLine` inside the run's container. A fired scheduled
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
        if detail.kind.as_deref() == Some("hook_completed") {
            push_hook_message(lines, detail, width, grouped);
            return;
        }
    }
    // Only client-owned notices (app-server crash, failed suspend) render their
    // message. Server notices mount no widget unless handled above, matching
    // Python's `_handle_notice` dispatch (e.g. `session_title_updated` only
    // updates the title).
    if !local {
        return;
    }
    let color = match notice.level.as_str() {
        "error" => theme::error(),
        _ => theme::warning(),
    };
    lines.push(Line::from(vec![
        inset_prefix(true, theme::text(color)),
        Span::styled(notice.message.to_string(), theme::text(color)),
    ]));
}

/// Python `HookSystemMessageLine`: `{severity icon} [{hook_name}] {content}` — the
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
    // Python's pre-tool container carries `hook-before-tool`, which matches no
    // TCSS rule, so the line renders with no margin of its own.
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

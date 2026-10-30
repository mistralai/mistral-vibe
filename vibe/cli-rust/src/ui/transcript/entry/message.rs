//! Message-entry rendering.

use std::sync::Arc;

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use super::startup;
use super::status::{
    push_command_error, push_command_status, push_compact_status, push_teleport_status,
};
use super::user::{push_user, UserView};
use super::{QueueView, RenderedEntry};
use crate::selection::{fold, Fold};
use crate::server::MessageEntry;
use crate::transcript::TranscriptEntry;
use crate::ui::markdown::{self, LinkedLines};
use crate::ui::theme;

pub(super) fn render(
    entry: &TranscriptEntry<'_>,
    message: &MessageEntry,
    width: u16,
    queue: QueueView,
    rewind: bool,
    pulse_frame: usize,
    cache: Option<&mut markdown::MarkdownCache>,
) -> RenderedEntry {
    let mut lines = LinkedLines::default();
    match message.role.as_str() {
        "user" => push_user(
            &mut lines,
            message,
            width,
            UserView::of(entry, queue.selected || rewind),
        ),
        "teleport_user" => push_user(&mut lines, message, width, UserView::default()),
        "teleport_status" | "teleport_complete" => push_teleport_status(
            &mut lines,
            &message_text(message),
            message.role == "teleport_complete",
            entry.entry.in_progress(),
            pulse_frame,
        ),
        "command_result" => push_command_result(&mut lines, &message_text(message), width, false),
        "agent_statistics" => push_command_result(&mut lines, &message_text(message), width, true),
        "command_status" => push_command_status(
            &mut lines,
            &message_text(message),
            width,
            "✓",
            theme::status_ready(),
        ),
        "command_warning" => push_command_status(
            &mut lines,
            &message_text(message),
            width,
            "⚠",
            theme::warning(),
        ),
        "command_error" => push_command_error(&mut lines, &message_text(message), width),
        "warning" => startup::push_warning(&mut lines, &message_text(message), width),
        "subagent_info" => {
            push_subagent_severity(&mut lines, &message_text(message), width, theme::success())
        }
        "subagent_error" => {
            push_subagent_severity(&mut lines, &message_text(message), width, theme::error())
        }
        "whats_new" => startup::push_whats_new(&mut lines, &message_text(message), width),
        "progress_status" => push_compact_status(
            &mut lines,
            &message_text(message),
            entry.entry.in_progress(),
            false,
            pulse_frame,
        ),
        _ => {
            let prepared = match cache {
                Some(cache) => {
                    cache.prepare(entry.index, entry.rev, width, theme::active_index(), || {
                        message_text(message)
                    })
                }
                None => Arc::new(markdown::prepare_uncached(&message_text(message), width)),
            };
            return RenderedEntry::prepared(prepared, message.role == "assistant");
        }
    }
    RenderedEntry::owned(lines)
}

pub(super) fn message_text(message: &MessageEntry) -> String {
    message.text("\n")
}

/// A content command's markdown, indented like an assistant reply with tight heading margins.
pub(super) fn push_command_result(
    lines: &mut LinkedLines,
    text: &str,
    width: u16,
    muted_emphasis: bool,
) {
    let mut body = markdown::command_result_linked(text, width);
    if muted_emphasis {
        body.restyle(mute_emphasis);
    }
    lines.append(body);
}

/// Statistics text: emphasis is secondary text, muted instead of italic.
fn mute_emphasis(span: &mut Span<'static>) {
    if span.style.add_modifier.contains(Modifier::ITALIC) {
        span.style = span
            .style
            .remove_modifier(Modifier::ITALIC)
            .patch(theme::muted_style());
    }
}

/// A user message with a severity: a heavy left border in the severity color,
/// the content colored the same, one blank row above, no prompt char and no
/// separator.
fn push_subagent_severity(
    lines: &mut LinkedLines,
    text: &str,
    width: u16,
    color: ratatui::style::Color,
) {
    lines.push_gap();
    let style = theme::text(color);
    let border = Style::default().fg(color);
    let body_width = (width.saturating_sub(3)) as usize;
    for (row, gap) in fold::wrap_hard(text, body_width) {
        let line = Line::from(vec![
            Span::styled("┃ ", border),
            Span::styled(row, style),
            Span::raw(" "),
        ]);
        lines.push_folded(line, Fold::hung(gap, 2));
    }
}

#[cfg(test)]
mod tests;

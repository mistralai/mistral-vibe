//! Message-entry rendering.

use std::sync::Arc;

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use super::bordered::{inset_body_width, inset_prefix};
use super::startup;
use super::status::{push_compact_status, push_teleport_status};
use super::user::{push_user, UserView};
use super::{QueueView, RenderedEntry};
use crate::selection::{fold, Fold};
use crate::server::{MessageContent, MessageEntry};
use crate::transcript::TranscriptEntry;
use crate::ui::markdown::{self, LinkedLines};
use crate::ui::theme;
use crate::utils::clean;

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
        "command" => push_command(&mut lines, &message_text(message)),
        "command_result" => push_command_result(&mut lines, &message_text(message), width, false),
        "agent_statistics" => push_command_result(&mut lines, &message_text(message), width, true),
        "command_error" => push_command_error(&mut lines, &message_text(message), width),
        "warning" => startup::push_warning(&mut lines, &message_text(message), width),
        "subagent_info" => {
            push_subagent_severity(&mut lines, &message_text(message), width, theme::success())
        }
        "subagent_error" => {
            push_subagent_severity(&mut lines, &message_text(message), width, theme::error())
        }
        "whats_new" => startup::push_whats_new(
            &mut lines,
            &message_text(message),
            width,
            entry.after_history,
        ),
        "custom_tools_deprecation" => {
            startup::push_guttered(&mut lines, &message_text(message), width, theme::warning())
        }
        "compact_status" => push_compact_status(
            &mut lines,
            &message_text(message),
            entry.entry.in_progress(),
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
    message
        .content
        .iter()
        .filter_map(|block| match block {
            MessageContent::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn push_command(lines: &mut LinkedLines, text: &str) {
    let style = theme::text(theme::ORANGE).add_modifier(Modifier::BOLD);
    lines.push_gap();
    lines.push_gap();
    lines.push(Line::from(Span::styled(
        format!("/ {}", text.trim_start_matches('/')),
        style,
    )));
}

pub(super) fn push_command_result(
    lines: &mut LinkedLines,
    text: &str,
    width: u16,
    muted_emphasis: bool,
) {
    // The command renderer already carries the `.user-command-content` tcss:
    // first/last block margins zeroed and every heading tight against its
    // content, so only the leading blank rows the plain renderer inserts
    // still need dropping.
    let body = markdown::command_result_linked(text, width.saturating_sub(2));
    let folds = body.folds().to_vec();
    let mut body: Vec<_> = body.into_lines().into_iter().zip(folds).collect();
    while body
        .first()
        .is_some_and(|(line, _)| markdown::is_blank_line(line))
    {
        body.remove(0);
    }
    let last = body.len().saturating_sub(1);
    for (index, (mut line, fold)) in body.into_iter().enumerate() {
        let trimmed = trim_left_padding(&mut line);
        if muted_emphasis {
            mute_emphasis(&mut line);
        }
        let border = inset_prefix(index == last, Style::default());
        let hang = border.width() as u16;
        line.spans.insert(0, border);
        let fold = fold.map(|fold| Fold {
            hang: (fold.hang + hang).saturating_sub(trimmed),
            ..fold
        });
        lines.push_folded(line, fold);
    }
}

/// Python `.agent-statistics`: emphasis is secondary text, muted instead of italic.
fn mute_emphasis(line: &mut Line<'static>) {
    for span in &mut line.spans {
        if span.style.add_modifier.contains(Modifier::ITALIC) {
            span.style = span
                .style
                .remove_modifier(Modifier::ITALIC)
                .patch(theme::muted_style());
        }
    }
}

fn push_command_error(lines: &mut LinkedLines, text: &str, width: u16) {
    let style = theme::text(theme::error()).add_modifier(Modifier::BOLD);
    // Python `ErrorMessage.compose`: sanitize, prefix, wrap the whole content.
    let content = format!("Error: {}", clean::clean_output(text));
    let rows = fold::wrap_hard(&content, inset_body_width(width) as usize);
    let last = rows.len().saturating_sub(1);
    for (index, (row, gap)) in rows.into_iter().enumerate() {
        let border = inset_prefix(index == last, Style::default());
        let hang = border.width() as u16;
        let line = Line::from(vec![border, Span::styled(row, style)]);
        lines.push_folded(line, Fold::hung(gap, hang));
    }
}

/// Python `UserMessage(severity=...)`: a heavy left border in the severity
/// color, the content colored the same, one blank row above (the wrapper's
/// `margin-top: 1`), no prompt char and no separator.
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

/// Drop up to two leading pad cells, returning how many it dropped.
fn trim_left_padding(line: &mut Line<'static>) -> u16 {
    let mut remaining = 2;
    for span in &mut line.spans {
        if remaining == 0 || !span.content.starts_with(' ') {
            break;
        }
        let trim = span
            .content
            .chars()
            .take_while(|character| *character == ' ')
            .count()
            .min(remaining);
        span.content = span.content.chars().skip(trim).collect();
        remaining -= trim;
    }
    (2 - remaining) as u16
}

#[cfg(test)]
mod tests;

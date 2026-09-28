//! Message-entry rendering.

use std::sync::Arc;

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use super::bordered::{body_width, prefix};
use super::startup;
use super::status::push_compact_status;
use super::user::push_user;
use super::{QueueView, RenderedEntry};
use crate::server::{MessageContent, MessageEntry};
use crate::transcript::TranscriptEntry;
use crate::ui::{markdown, theme};
use crate::utils::clean;
use crate::utils::text;

pub(super) fn render(
    entry: &TranscriptEntry<'_>,
    message: &MessageEntry,
    width: u16,
    queue: QueueView,
    rewind: bool,
    pulse_frame: usize,
    cache: Option<&mut markdown::MarkdownCache>,
) -> RenderedEntry {
    let mut lines = Vec::new();
    match message.role.as_str() {
        "user" => push_user(
            &mut lines,
            message,
            width,
            entry.pending,
            queue.selected || rewind,
            entry.follows_user,
            entry.followed_by_user,
        ),
        "command" => push_command(&mut lines, &message_text(message)),
        "command_result" => push_command_result(&mut lines, &message_text(message), width),
        "command_error" => push_command_error(&mut lines, &message_text(message), width),
        "warning" => startup::push_warning(&mut lines, &message_text(message), width),
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
    let mut rendered = RenderedEntry::owned(lines);
    if message.role == "user" {
        rendered.links = super::user::attachment_links(message);
        rendered.link_kind = markdown::LinkKind::Attachment;
    }
    // The guttered banners carry markdown links; `warning` is a plain
    // `NoMarkupStatic` and never a link source.
    if matches!(
        message.role.as_str(),
        "whats_new" | "custom_tools_deprecation"
    ) {
        rendered.links = markdown::targets(&message_text(message));
    }
    rendered
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

fn push_command(lines: &mut Vec<Line<'static>>, text: &str) {
    let style = theme::text(theme::ORANGE).add_modifier(Modifier::BOLD);
    lines.push(Line::from(""));
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        format!("/ {}", text.trim_start_matches('/')),
        style,
    )));
}

pub(super) fn push_command_result(lines: &mut Vec<Line<'static>>, text: &str, width: u16) {
    let mut body = markdown::render(text, width.saturating_sub(2));
    let is_blank =
        |line: &Line<'static>| line.spans.iter().all(|span| span.content.trim().is_empty());
    while body.first().is_some_and(&is_blank) {
        body.remove(0);
    }
    // Textual's Markdown container sits the first block against the second with
    // no blank line, then separates each later block by one. Drop only the first
    // paragraph break (wherever the first block ends, even when it soft-wraps),
    // keeping every later blank.
    if let Some(position) = body.iter().position(&is_blank) {
        body.remove(position);
    }
    let last = body.len().saturating_sub(1);
    for (index, mut line) in body.into_iter().enumerate() {
        trim_left_padding(&mut line);
        line.spans
            .insert(0, prefix(index == last, Style::default()));
        lines.push(line);
    }
}

fn push_command_error(lines: &mut Vec<Line<'static>>, text: &str, width: u16) {
    let style = theme::text(theme::error()).add_modifier(Modifier::BOLD);
    // Python `ErrorMessage.compose`: sanitize, prefix, wrap the whole content.
    let content = format!("Error: {}", clean::clean_output(text));
    let rows: Vec<String> = text::wrap_hard(&content, body_width(width) as usize);
    let last = rows.len().saturating_sub(1);
    for (index, row) in rows.into_iter().enumerate() {
        lines.push(Line::from(vec![
            prefix(index == last, Style::default()),
            Span::styled(row, style),
        ]));
    }
}

fn trim_left_padding(line: &mut Line<'static>) {
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
}

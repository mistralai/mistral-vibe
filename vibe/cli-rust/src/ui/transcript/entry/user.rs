//! User prompts and the header the queued ones sit under.

use std::path::{Path, PathBuf};

use ratatui::style::Modifier;
use ratatui::text::{Line, Span};

use super::super::super::theme;
use crate::server::{ImageAttachment, ImageSource, MessageContent, MessageEntry};

const QUEUE_HEADER_LABEL: &str = "» Queued";
/// Shown once the server holds the queue: Enter releases it (Python `PAUSED_LABEL`).
const QUEUE_PAUSED_PREFIX: &str = "» Queued — press ";
const QUEUE_PAUSED_SUFFIX: &str = " to send, type to add";

/// Python `QueueHeaderMessage`: one gap row, the bold-italic label, then the
/// same expanding separator a user message paints.
pub(super) fn push_queue_header(lines: &mut Vec<Line<'static>>, width: u16, paused: bool) {
    let label = theme::text(theme::ORANGE).add_modifier(Modifier::BOLD | Modifier::ITALIC);
    lines.push(Line::from(""));
    lines.push(match paused {
        false => Line::from(Span::styled(QUEUE_HEADER_LABEL, label)),
        // The shortcut only recolors the label it sits in (Python `SHORTCUT_STYLE`).
        true => Line::from(vec![
            Span::styled(QUEUE_PAUSED_PREFIX, label),
            Span::styled("Enter", label.fg(theme::primary())),
            Span::styled(QUEUE_PAUSED_SUFFIX, label),
        ]),
    });
    push_separator(lines, width);
}

fn push_separator(lines: &mut Vec<Line<'static>>, width: u16) {
    lines.push(Line::from(Span::styled(
        "─".repeat(width.max(1) as usize),
        theme::muted_style(),
    )));
}

/// A queued prompt is packed against its neighbours and italic, and drops its
/// separator; the highlighted one reverses its content (Python `.queue-selected`).
pub(super) fn push_user(
    lines: &mut Vec<Line<'static>>,
    message: &MessageEntry,
    width: u16,
    pending: bool,
    selected: bool,
    follows_user: bool,
    followed_by_user: bool,
) {
    let emphasis = if pending {
        Modifier::ITALIC
    } else {
        Modifier::BOLD
    };
    let prompt = theme::text(theme::ORANGE).add_modifier(emphasis);
    let content = match selected {
        true => theme::text(theme::foreground()).add_modifier(Modifier::BOLD | Modifier::REVERSED),
        false => theme::text(theme::foreground()).add_modifier(emphasis),
    };
    if !pending && !follows_user {
        lines.push(Line::from(""));
        lines.push(Line::from(""));
    }
    // A settled message's content widget spans the row (`width: 1fr`), so its
    // rewind highlight reverses the padding too; a queued one is `width: auto`.
    let padded = selected && !pending;
    let content_width = width.saturating_sub(2) as usize;
    for (index, text_line) in super::message_text(message).lines().enumerate() {
        let marker = if index == 0 { "> " } else { "  " };
        let mut body = text_line.to_string();
        if padded {
            let pad = content_width.saturating_sub(body.chars().count());
            body.push_str(&" ".repeat(pad));
        }
        lines.push(Line::from(vec![
            Span::styled(marker, prompt),
            Span::styled(body, content),
        ]));
    }
    for content in &message.content {
        let MessageContent::Image { attachment } = content else {
            continue;
        };
        let label = attachment_label(attachment);
        let label_style = match &attachment.source {
            ImageSource::File { .. } => {
                theme::dim(theme::success()).add_modifier(Modifier::UNDERLINED)
            }
            ImageSource::Inline { .. } => theme::muted_style(),
        };
        lines.push(Line::from(vec![
            Span::styled("  └ attached image: ", theme::muted_style()),
            Span::styled(label, label_style),
        ]));
    }
    if !pending && !followed_by_user {
        push_separator(lines, width);
    }
}

pub(super) fn attachment_links(message: &MessageEntry) -> Vec<(String, String)> {
    message
        .content
        .iter()
        .filter_map(|content| match content {
            MessageContent::Image { attachment } => {
                Some((attachment_label(attachment), attachment.file_url()?))
            }
            _ => None,
        })
        .collect()
}

fn attachment_label(attachment: &ImageAttachment) -> String {
    let alias = PathBuf::from(&attachment.alias);
    if !alias.is_absolute() {
        return attachment.alias.clone();
    }
    display_path(&alias)
}

fn display_path(path: &Path) -> String {
    let Some(home) = std::env::var_os("HOME").map(PathBuf::from) else {
        return path.to_string_lossy().into_owned();
    };
    let Ok(relative) = path.strip_prefix(home) else {
        return path.to_string_lossy().into_owned();
    };
    if relative.as_os_str().is_empty() {
        return "~".to_owned();
    }
    Path::new("~").join(relative).to_string_lossy().into_owned()
}

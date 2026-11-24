//! User prompts and the header the queued ones sit under.

use std::path::{Path, PathBuf};

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use super::super::super::hint_line;
use super::super::super::markdown::{self, LinkKind, LinkedLines, Sc};
use super::super::super::theme;
use super::loop_prompt::push_loop_prompt;
use super::user_text;
use crate::hints::{action, key, Hint};
use crate::selection::Fold;
use crate::server::{ImageAttachment, ImageSource, MessageContent, MessageEntry};
use crate::transcript::{FiredLoop, TranscriptEntry};

const QUEUE_HEADER_MARKER: &str = "» ";
const QUEUE_HEADER_LABEL: &str = "Queued";
/// Shown once the server holds the queue: Enter releases it, Up edits it.
const QUEUE_PAUSED_HINTS: &[Hint] = &[(key::ENTER, action::SEND), (key::UP, action::EDIT)];
const ATTACHED_IMAGE: &str = "  ┝ attached image: ";
const LAST_ATTACHED_IMAGE: &str = "  └ attached image: ";
/// The alias the harness gives a resumed image, which forgets its placeholder.
const GENERIC_IMAGE_ALIAS: &str = "image";

/// The queue header: a gap, the orange marker, the bold-italic label and the queue's keys, then a user message's separator.
pub(super) fn push_queue_header(lines: &mut LinkedLines, width: u16, paused: bool, hints: &[Hint]) {
    let emphasis = Modifier::BOLD | Modifier::ITALIC;
    lines.push_gap();
    let mut spans = vec![
        Span::styled(
            QUEUE_HEADER_MARKER,
            theme::text(theme::ORANGE).add_modifier(emphasis),
        ),
        Span::styled(
            QUEUE_HEADER_LABEL,
            theme::text(theme::foreground()).add_modifier(emphasis),
        ),
    ];
    let paused_keys: &[Hint] = if paused { QUEUE_PAUSED_HINTS } else { &[] };
    let label_width: usize = spans.iter().map(Span::width).sum();
    // The widest key list that fits the one measured row: the paused queue's Enter and ↑ drop last.
    let fitting = (0..=hints.len()).rev().find_map(|kept| {
        let keys: Vec<Hint> = paused_keys.iter().chain(&hints[..kept]).copied().collect();
        let mut tail = vec![Span::styled(" — ", hint_line::action_style())];
        tail.extend(hint_line::inline_spans(&keys));
        let tail_width: usize = tail.iter().map(Span::width).sum();
        (!keys.is_empty() && label_width + tail_width <= usize::from(width)).then_some(tail)
    });
    spans.extend(fitting.unwrap_or_default());
    lines.push(Line::from(spans));
    push_separator(lines, width);
}

pub(super) fn push_separator(lines: &mut LinkedLines, width: u16) {
    lines.push(Line::from(Span::styled(
        "─".repeat(width.max(1) as usize),
        theme::muted_style(),
    )));
}

/// The per-entry state a user prompt renders with.
#[derive(Clone, Copy, Default)]
pub(super) struct UserView<'a> {
    pub pending: bool,
    pub selected: bool,
    pub follows_user: bool,
    pub followed_by_user: bool,
    /// Steered into the running turn: packed closer, marked `↳`, no separator.
    pub steer: bool,
    /// The scheduled loop whose firing sent this prompt.
    pub fired_loop: Option<&'a FiredLoop>,
    /// Whether a loop prompt shows its instructions under its title.
    pub expanded: bool,
    /// The transcript entry id, naming its inline images for a click to open.
    pub entry_id: &'a str,
}

impl<'a> UserView<'a> {
    pub(super) fn of(entry: &TranscriptEntry<'a>, selected: bool, expanded: bool) -> Self {
        Self {
            pending: entry.pending,
            selected,
            follows_user: entry.follows_user,
            followed_by_user: entry.followed_by_user,
            steer: entry.steer,
            fired_loop: entry.fired_loop,
            expanded,
            entry_id: entry.id,
        }
    }
}

/// Marker and text styles: primary marker for a loop, italic when queued, reversed when highlighted.
pub(super) fn prompt_styles(looped: bool, pending: bool, selected: bool) -> (Style, Style) {
    let emphasis = if pending {
        Modifier::ITALIC
    } else {
        Modifier::BOLD
    };
    let marker_color = match looped {
        true => theme::primary(),
        false => theme::ORANGE,
    };
    let content = match selected {
        true => theme::text(theme::foreground()).add_modifier(Modifier::BOLD | Modifier::REVERSED),
        false => theme::text(theme::foreground()).add_modifier(emphasis),
    };
    (theme::text(marker_color).add_modifier(emphasis), content)
}

/// A queued prompt is packed against its neighbours and italic, and drops its
/// separator; the highlighted one reverses its content. A loop's prompt folds under its title.
pub(super) fn push_user(
    lines: &mut LinkedLines,
    message: &MessageEntry,
    width: u16,
    view: UserView<'_>,
) {
    let UserView {
        pending,
        selected,
        follows_user,
        followed_by_user,
        steer,
        fired_loop,
        expanded,
        entry_id,
    } = view;
    let (prompt, content) = prompt_styles(fired_loop.is_some(), pending, selected);
    if !pending && !follows_user {
        lines.push_gap();
        if !steer {
            lines.push_gap();
        }
    }
    // A settled message's content widget spans the row (`width: 1fr`), so its
    // rewind highlight reverses the padding too; a queued one is `width: auto`.
    let padded = selected && !pending;
    let content_width = width.saturating_sub(2) as usize;
    if let Some(fired) = fired_loop {
        let text = super::message_text(message);
        let styles = (prompt, content);
        push_loop_prompt(lines, &text, fired, expanded, styles, content_width);
        if !pending && !followed_by_user {
            push_separator(lines, width);
        }
        return;
    }
    let marker = match steer {
        true => user_text::STEER_MARKER,
        false => user_text::PROMPT_MARKER,
    };
    let styles = (prompt, content);
    let collapsed = user_text::push(lines, message, width, (marker, styles), padded, usize::MAX);
    let images: Vec<&ImageAttachment> = message
        .content
        .iter()
        .filter_map(|content| match content {
            MessageContent::Image { attachment } => Some(attachment),
            _ => None,
        })
        .collect();
    let names = resumed_image_names(&collapsed.text, images.len());
    for (index, attachment) in images.iter().enumerate() {
        let label = match (&names, attachment.alias == GENERIC_IMAGE_ALIAS) {
            (Some(names), true) => alias_label(&names[index]),
            _ => alias_label(&attachment.alias),
        };
        let connector = match index + 1 == images.len() {
            true => LAST_ATTACHED_IMAGE,
            false => ATTACHED_IMAGE,
        };
        let link = match &attachment.source {
            ImageSource::File { .. } => attachment
                .file_url()
                .map(|url| lines.link(url, LinkKind::Attachment)),
            // A resumed image arrives inline: a click writes it to a temporary file.
            ImageSource::Inline { .. } if !entry_id.is_empty() => Some(lines.link(
                crate::inline_images::target(entry_id, index),
                LinkKind::InlineImage,
            )),
            ImageSource::Inline { .. } => None,
        };
        let label_style = match link {
            Some(_) => theme::dim(theme::success()).add_modifier(Modifier::UNDERLINED),
            None => theme::muted_style(),
        };
        let chars: Vec<Sc> = connector
            .chars()
            .map(|c| (c, theme::muted_style(), None))
            .chain(label.chars().map(|c| (c, label_style, link)))
            .collect();
        for (row, gap) in markdown::wrap_chars_folded(&chars, width as usize) {
            lines.push_row(Vec::new(), &row);
            lines.fold_last(Fold::hung(gap, 0));
        }
    }
    if !pending && !followed_by_user && !steer {
        push_separator(lines, width);
    }
}

/// The names a resumed prompt gave its images, which the harness forgets on
/// resume: its `[Image #N]` placeholders, else its `@` image mentions, in order.
fn resumed_image_names(text: &str, count: usize) -> Option<Vec<String>> {
    let placeholders = crate::image_placeholders::labels_in(text);
    if placeholders.len() == count {
        return Some(placeholders.into_iter().map(str::to_owned).collect());
    }
    let mentions = crate::paste_path::image_mentions_in(text);
    (mentions.len() == count).then_some(mentions)
}

fn alias_label(alias: &str) -> String {
    let path = PathBuf::from(alias);
    if !path.is_absolute() {
        return alias.to_owned();
    }
    display_path(&path)
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

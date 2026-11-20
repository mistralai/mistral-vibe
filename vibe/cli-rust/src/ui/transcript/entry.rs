//! Transcript entry formatting, kept separate from viewport and scroll layout.

use ratatui::text::{Line, Span};

mod bordered;
mod effect;
mod effect_body;
mod loop_prompt;
mod message;
mod notice;
mod raw;
mod reasoning;
mod rendered;
pub(super) mod startup;
mod status;
mod turn_details;
mod user;
mod user_text;
mod view;

use super::super::markdown::{self, LinkedLines};
use super::super::theme;
use crate::transcript::TranscriptEntry;
use bordered::{inset_prefix, prefix_group_body, push_group_header, BORDER_WIDTH, INSET_WIDTH};
use effect::{push_effect, EffectView};
use message::message_text;
use notice::push_notice;
use reasoning::push_reasoning;
pub(super) use rendered::{RenderedEntry, RenderedParts};
use status::push_checkpoint;
pub(super) use turn_details::{DetailsView, INDENT as DETAILS_INDENT};
use user::push_queue_header;
pub(super) use user_text::sticky;
pub(super) use view::{ExpansionView, QueueView};

/// The rendered content and hit-test metadata for one transcript entry.
pub(super) fn render(
    entry: &TranscriptEntry<'_>,
    width: u16,
    expansion: ExpansionView<'_>,
    pulse_frame: usize,
    queue: QueueView,
    rewind: bool,
    cache: Option<&mut markdown::MarkdownCache>,
) -> RenderedEntry {
    let Some(details) = expansion.details else {
        return render_entry(entry, width, expansion, pulse_frame, (queue, rewind), cache);
    };
    turn_details::render(entry.index, details, width, |inner| {
        render_entry(entry, inner, expansion, pulse_frame, (queue, rewind), cache)
    })
}

fn render_entry(
    entry: &TranscriptEntry<'_>,
    width: u16,
    expansion: ExpansionView<'_>,
    pulse_frame: usize,
    (queue, rewind): (QueueView, bool),
    cache: Option<&mut markdown::MarkdownCache>,
) -> RenderedEntry {
    use crate::server::HistoryEntry as H;
    if let H::Message(value) = entry.entry {
        let (selected, expanded) = (queue.selected || rewind, expansion.entry);
        let view = message::MessageView {
            selected,
            expanded,
            raw: expansion.raw,
        };
        let mut rendered = message::render(entry, value, width, view, pulse_frame, cache);
        if entry.queue_header {
            let mut header = LinkedLines::default();
            push_queue_header(&mut header, width, queue.paused, queue.hints);
            rendered.prepend(header);
        }
        return rendered;
    }

    let mut lines = LinkedLines::default();
    if let Some(group) = &entry.group {
        if group.first {
            lines.push_gap();
            push_group_header(
                &mut lines,
                group,
                expansion.group,
                !group.finalized,
                pulse_frame,
                width,
            );
        }
        if !expansion.group {
            return RenderedEntry::owned(lines);
        }
    }
    let mut content = LinkedLines::default();
    let content_width = width.saturating_sub(if entry.group.is_some() {
        BORDER_WIDTH
    } else {
        0
    });
    let in_progress = entry.entry.in_progress();
    match entry.entry {
        H::Reasoning(reasoning) => {
            push_group_gap(&mut content, entry.group.is_some());
            push_reasoning(
                &mut content,
                &reasoning.text,
                in_progress,
                entry.local,
                expansion.entry,
                pulse_frame,
                content_width,
            );
        }
        H::Effect(effect) => {
            push_group_gap(&mut content, entry.group.is_some());
            push_effect(
                &mut content,
                effect,
                EffectView {
                    index: entry.index,
                    rev: entry.rev,
                    in_progress,
                    local: entry.local,
                    expanded: expansion.entry,
                    grouped: entry.group.is_some(),
                    stream_delta: entry.stream_delta,
                },
                entry.attached_output,
                pulse_frame,
                content_width,
                cache,
            );
        }
        H::Interrupt(_) => push_interrupt(&mut content),
        H::Callback(_) => {}
        H::Notice(notice) => push_notice(
            &mut content,
            notice,
            content_width,
            entry.local,
            entry.group.is_some(),
        ),
        H::Checkpoint(checkpoint) => push_checkpoint(
            &mut content,
            checkpoint,
            entry.local,
            in_progress,
            pulse_frame,
        ),
        H::Message(_) | H::Unknown => {}
    }
    if let Some(group) = &entry.group {
        prefix_group_body(&mut content, group.last);
    }
    lines.append(content);
    RenderedEntry::owned(lines)
}

/// Whether this entry paints a `/raw` agent message.
pub(super) fn is_raw(entry: &TranscriptEntry<'_>, raw: bool) -> bool {
    raw && matches!(entry.entry, crate::server::HistoryEntry::Message(message) if message.role == "assistant")
}

/// The `(border, gutter)` columns opening this entry's rows: its border glyphs, then an edit diff's gutter.
pub(super) fn chrome(entry: &TranscriptEntry<'_>, expanded: bool) -> (u16, u16) {
    use crate::server::HistoryEntry as H;
    let group = if entry.group.is_some() {
        BORDER_WIDTH
    } else {
        0
    };
    let (own, gutter) = match entry.entry {
        H::Effect(effect) => (
            BORDER_WIDTH,
            effect::diff_gutter(effect, entry.entry.in_progress(), expanded).unwrap_or(0),
        ),
        H::Interrupt(_) => (INSET_WIDTH, 0),
        H::Notice(_) if entry.local => (INSET_WIDTH, 0),
        _ => (0, 0),
    };
    (group + own, gutter)
}

/// `.tool-group` packs its members, so only the entry opening the group is preceded by a gap.
fn push_group_gap(lines: &mut LinkedLines, grouped: bool) {
    if !grouped {
        lines.push_gap();
    }
}

fn push_interrupt(lines: &mut LinkedLines) {
    lines.push(Line::from(vec![
        inset_prefix(theme::text(theme::foreground())),
        Span::styled(
            "Interrupted · What should Vibe do instead?",
            theme::text(theme::warning()),
        ),
    ]));
}

pub(super) fn expand_marker(expanded: bool) -> &'static str {
    if expanded {
        "⏷"
    } else {
        "⏵"
    }
}

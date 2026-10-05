//! Transcript entry formatting, kept separate from viewport and scroll layout.

use std::sync::Arc;

use ratatui::text::{Line, Span};

mod bordered;
mod effect;
mod effect_body;
mod message;
mod notice;
mod startup;
mod status;
mod user;

use super::super::markdown::{self, LinkedLines};
use super::super::{pulse, theme};
use crate::transcript::TranscriptEntry;
use bordered::{prefix, prefix_group_body, push_group_header};
use effect::{push_effect, EffectView};
use message::message_text;
use notice::push_notice;
use status::push_checkpoint;
use user::push_queue_header;

/// Per-frame queue state a queued prompt renders with.
#[derive(Clone, Copy, Default)]
pub(super) struct QueueView {
    /// Whether this prompt is the queue-mode highlight.
    pub selected: bool,
    /// Whether the server is holding the queue until the user resumes it.
    pub paused: bool,
}

#[derive(Clone, Copy)]
pub(super) struct ExpansionView {
    pub entry: bool,
    pub group: bool,
}

pub(super) struct RenderedEntry {
    lines: LinkedLines,
    prepared: Option<Arc<markdown::PreparedMarkdown>>,
    table_cells: bool,
}

pub(super) struct RenderedParts {
    pub lines: LinkedLines,
    pub prepared: Option<Arc<markdown::PreparedMarkdown>>,
}

impl RenderedEntry {
    fn owned(lines: LinkedLines) -> Self {
        Self {
            lines,
            prepared: None,
            table_cells: false,
        }
    }

    fn prepared(prepared: Arc<markdown::PreparedMarkdown>, table_cells: bool) -> Self {
        Self {
            lines: LinkedLines::default(),
            prepared: Some(prepared),
            table_cells,
        }
    }

    fn prepend(&mut self, mut prefix: LinkedLines) {
        match self.prepared.take() {
            Some(prepared) => prefix.append(prepared.linked().clone()),
            None => prefix.append(std::mem::take(&mut self.lines)),
        }
        self.lines = prefix;
    }

    pub fn lines(&self) -> &[Line<'static>] {
        self.prepared
            .as_ref()
            .map_or(self.lines.lines(), |prepared| prepared.lines())
    }

    pub fn prewrapped(&self, width: u16) -> bool {
        self.prepared.as_ref().map_or_else(
            || {
                width > 0
                    && self
                        .lines
                        .lines()
                        .iter()
                        .all(|line| line.width() <= width as usize)
            },
            |prepared| prepared.prewrapped(),
        )
    }

    pub fn table_cells(&self) -> &[markdown::TableCell] {
        match (&self.prepared, self.table_cells) {
            (Some(prepared), true) => prepared.cells(),
            _ => &[],
        }
    }

    pub fn into_parts(self) -> RenderedParts {
        RenderedParts {
            lines: self.lines,
            prepared: self.prepared,
        }
    }
}

/// The rendered content and hit-test metadata for one transcript entry.
pub(super) fn render(
    entry: &TranscriptEntry<'_>,
    width: u16,
    expansion: ExpansionView,
    pulse_frame: usize,
    queue: QueueView,
    rewind: bool,
    cache: Option<&mut markdown::MarkdownCache>,
) -> RenderedEntry {
    use crate::server::HistoryEntry as H;
    if let H::Message(value) = entry.entry {
        let mut rendered = message::render(entry, value, width, queue, rewind, pulse_frame, cache);
        if entry.queue_header {
            let mut header = LinkedLines::default();
            push_queue_header(&mut header, width, queue.paused);
            rendered.prepend(header);
        }
        return rendered;
    }

    let mut lines = LinkedLines::default();
    if let Some(group) = &entry.group {
        if group.first {
            lines.push(Line::from(""));
            push_group_header(
                &mut lines,
                group,
                expansion.group,
                !group.finalized,
                pulse_frame,
            );
        }
        if !expansion.group {
            return RenderedEntry::owned(lines);
        }
    }
    let mut content = LinkedLines::default();
    let content_width = width.saturating_sub(if entry.group.is_some() { 4 } else { 0 });
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
        // Server-owned compaction is represented by `compact_status`; only
        // client-owned checkpoints render independently.
        H::Checkpoint(checkpoint) if entry.local => push_checkpoint(
            &mut content,
            checkpoint.message.as_deref().unwrap_or(&checkpoint.kind),
        ),
        H::Message(_) | H::Checkpoint(_) | H::Unknown => {}
    }
    if let Some(group) = &entry.group {
        prefix_group_body(&mut content, group.last);
    }
    lines.append(content);
    RenderedEntry::owned(lines)
}

/// Gutter width of the edit diff this entry paints, or `None` when it paints none.
pub(super) fn diff_gutter(entry: &TranscriptEntry<'_>, expanded: bool) -> Option<u16> {
    match entry.entry {
        crate::server::HistoryEntry::Effect(effect) => {
            effect::diff_gutter(effect, entry.entry.in_progress(), expanded)
        }
        _ => None,
    }
}

/// `.tool-group` packs its members, so only the entry opening the group is preceded by a gap.
fn push_group_gap(lines: &mut LinkedLines, grouped: bool) {
    if !grouped {
        lines.push(Line::from(""));
    }
}

fn push_reasoning(
    lines: &mut LinkedLines,
    text: &str,
    in_progress: bool,
    local: bool,
    expanded: bool,
    pulse_frame: usize,
) {
    let marker = if in_progress {
        pulse::glyph(pulse_frame).to_string()
    } else if local {
        "■".to_string()
    } else {
        expand_marker(expanded).to_string()
    };
    let label = if in_progress { "Thinking" } else { "Thought" };
    let style = theme::muted_style();
    lines.push(Line::from(vec![
        Span::styled(format!("{marker} "), style),
        Span::styled(label, style),
    ]));
    if expanded {
        for text_line in text.lines() {
            lines.push(Line::from(Span::styled(format!("  {text_line}"), style)));
        }
    }
}

fn push_interrupt(lines: &mut LinkedLines) {
    lines.push(Line::from(vec![
        prefix(true, theme::text(theme::foreground())),
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

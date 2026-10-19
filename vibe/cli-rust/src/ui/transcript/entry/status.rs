//! Settled status rows: the compact indicator and the client checkpoint message.

use ratatui::style::Modifier;
use ratatui::text::{Line, Span};

use super::super::super::markdown::{LinkKind, LinkedLines, Sc};
use super::super::super::{pulse, theme};
use crate::server::CheckpointEntry;
use crate::transcript::grouping::{compaction_failed, paints_compaction};

/// A single status row — pulse
/// spinner while in progress, ✓ on success, ✕ on error. No border. A blank
/// line separates it from the preceding row.
pub(super) fn push_compact_status(
    lines: &mut LinkedLines,
    text: &str,
    in_progress: bool,
    failed: bool,
    pulse_frame: usize,
) {
    let (glyph, color) = if in_progress {
        (pulse::glyph(pulse_frame).to_string(), theme::foreground())
    } else if failed || text.starts_with("Error:") {
        ("✕".to_string(), theme::error())
    } else {
        ("✓".to_string(), theme::status_ready())
    };
    lines.push_gap();
    lines.push(Line::from(vec![
        Span::styled(format!("{glyph} "), theme::text(color)),
        Span::styled(text.to_string(), theme::text(theme::foreground())),
    ]));
}

/// A client checkpoint settles as a status message; a server compaction paints
/// as a compact status row; other server checkpoints paint nothing.
pub(super) fn push_checkpoint(
    lines: &mut LinkedLines,
    checkpoint: &CheckpointEntry,
    local: bool,
    in_progress: bool,
    pulse_frame: usize,
) {
    let message = checkpoint.message.as_deref().unwrap_or(&checkpoint.kind);
    if local {
        push_settled(lines, message);
    } else if paints_compaction(checkpoint) {
        let failed = compaction_failed(checkpoint);
        push_compact_status(lines, message, in_progress, failed, pulse_frame);
    }
}

/// A settled status message: the `✓` indicator then its text, whose extra lines
/// align under the first one.
fn push_settled(lines: &mut LinkedLines, message: &str) {
    lines.push_gap();
    for (index, text) in message.lines().enumerate() {
        let marker = if index == 0 { "✓ " } else { "  " };
        lines.push(Line::from(vec![
            Span::styled(marker, theme::text(theme::status_ready())),
            Span::styled(text.to_string(), theme::text(theme::foreground())),
        ]));
    }
}

/// Teleport status: pulse, then ✓ with the link to `text` or ✕ once cancelled.
pub(super) fn push_teleport_status(
    lines: &mut LinkedLines,
    text: &str,
    complete: bool,
    in_progress: bool,
    pulse_frame: usize,
) {
    let (glyph, color) = if in_progress {
        (pulse::glyph(pulse_frame).to_string(), theme::foreground())
    } else if complete {
        ("✓".to_string(), theme::status_ready())
    } else {
        ("✕".to_string(), theme::error())
    };
    let body = theme::text(theme::foreground());
    let mut spans = vec![Span::styled(format!("{glyph} "), theme::text(color))];
    lines.push_gap();
    if !complete {
        spans.push(Span::styled(text.to_string(), body));
        lines.push(Line::from(spans));
        return;
    }
    spans.push(Span::styled("Teleported to ", body));
    let link = Some(lines.link(text.to_owned(), LinkKind::External));
    let style = theme::text(theme::md_link()).add_modifier(Modifier::UNDERLINED);
    let label: Vec<Sc> = TELEPORT_LINK_LABEL
        .chars()
        .map(|c| (c, style, link))
        .collect();
    lines.push_row(spans, &label);
}

const TELEPORT_LINK_LABEL: &str = "Vibe Code Web";

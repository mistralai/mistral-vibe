//! A reasoning trace: its `Thinking`/`Thought` row, folding its text.

use ratatui::text::{Line, Span};

use super::expand_marker;
use crate::selection::{fold, Fold};
use crate::ui::markdown::LinkedLines;
use crate::ui::{pulse, theme};

pub(super) fn push_reasoning(
    lines: &mut LinkedLines,
    text: &str,
    in_progress: bool,
    local: bool,
    expanded: bool,
    pulse_frame: usize,
    width: u16,
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
    if !expanded {
        return;
    }
    // `.reasoning-message-content` pads two cells on each side.
    let body_width = width.saturating_sub(4) as usize;
    for line in text.lines() {
        for (row, gap) in fold::wrap_hard(line, body_width) {
            let row = Line::from(Span::styled(format!("  {row}"), style));
            lines.push_folded(row, Fold::hung(gap, 2));
        }
    }
}

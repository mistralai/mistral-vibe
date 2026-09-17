//! The expanding border every result body sits in (Python `_bordered`).

use ratatui::style::Style;
use ratatui::text::{Line, Span};

use super::expand_marker;
use crate::transcript::grouping::{Group, Outcome};
use crate::ui::{pulse, theme};

/// Columns the border occupies before a row: two pad cells, the glyph, one pad.
const BORDER_WIDTH: u16 = 4;
/// Columns Textual's result container keeps free on the right.
const RESULT_MARGIN: u16 = 2;

/// Cells a bordered row may paint: the entry width less border and margin.
pub(super) fn body_width(width: u16) -> u16 {
    width
        .saturating_sub(BORDER_WIDTH)
        .saturating_sub(RESULT_MARGIN)
}

/// The border opening one row; `⎣` closes the run, as `ExpandingBorder` does.
pub(super) fn prefix(last: bool, style: Style) -> Span<'static> {
    Span::styled(if last { "  ⎣ " } else { "  ⎢ " }, style)
}

pub(super) fn push_group_header(
    lines: &mut Vec<Line<'static>>,
    group: &Group<'_>,
    expanded: bool,
    running: bool,
    pulse_frame: usize,
) {
    let marker = if running {
        pulse::glyph(pulse_frame).to_string()
    } else {
        expand_marker(expanded).to_owned()
    };
    let marker_style = if running {
        theme::text(theme::foreground())
    } else {
        match group.outcome {
            Outcome::Success => theme::text(theme::status_ready()),
            Outcome::Error => theme::text(theme::error()),
            Outcome::Muted => theme::muted_style(),
        }
    };
    lines.push(Line::from(vec![
        Span::styled(format!("{marker} "), marker_style),
        Span::styled(group.label(running), theme::dim(theme::effect_message())),
    ]));
}

pub(super) fn prefix_group_body(lines: &mut [Line<'static>], group_last: bool) {
    let last = lines.len().saturating_sub(1);
    for (index, line) in lines.iter_mut().enumerate() {
        line.spans
            .insert(0, prefix(group_last && index == last, theme::muted_style()));
    }
}

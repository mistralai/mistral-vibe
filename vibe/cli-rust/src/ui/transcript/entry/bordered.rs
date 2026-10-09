//! The expanding border every result body sits in (Python `_bordered`).

use ratatui::style::Style;
use ratatui::text::{Line, Span};

use super::expand_marker;
use crate::transcript::grouping::Group;
use crate::ui::markdown::LinkedLines;
use crate::ui::{pulse, theme};
use crate::utils::text;

/// Columns a tool border occupies: the glyph under its toggle arrow, then one pad.
pub(super) const BORDER_WIDTH: u16 = 2;
/// Columns Textual's result container keeps free on the right.
const RESULT_MARGIN: u16 = 2;
/// Pad cells a message, notice, or interrupt border keeps before its glyph.
const INSET_WIDTH: u16 = 2;

/// Cells a tool-bordered row may paint: the entry width less border and margin.
pub(super) fn body_width(width: u16) -> u16 {
    width
        .saturating_sub(BORDER_WIDTH)
        .saturating_sub(RESULT_MARGIN)
}

/// Cells an inset-bordered row may paint.
pub(super) fn inset_body_width(width: u16) -> u16 {
    body_width(width).saturating_sub(INSET_WIDTH)
}

/// The tool border opening one row; `⎣` closes the run, as `ExpandingBorder` does.
pub(super) fn prefix(last: bool, style: Style) -> Span<'static> {
    Span::styled(if last { "⎣ " } else { "⎢ " }, style)
}

/// The border of a message, notice, or interrupt, two cells in.
pub(super) fn inset_prefix(last: bool, style: Style) -> Span<'static> {
    Span::styled(if last { "  ⎣ " } else { "  ⎢ " }, style)
}

pub(super) fn push_group_header(
    lines: &mut LinkedLines,
    group: &Group<'_>,
    expanded: bool,
    running: bool,
    pulse_frame: usize,
    width: u16,
) {
    let label_style = theme::dim(theme::effect_message());
    let (marker, marker_style) = if running {
        (
            pulse::glyph(pulse_frame).to_string(),
            theme::text(theme::foreground()),
        )
    } else {
        (expand_marker(expanded).to_owned(), label_style)
    };
    lines.push(Line::from(vec![
        Span::styled(format!("{marker} "), marker_style),
        // One row, so hit-testing and cached heights never depend on the label.
        Span::styled(
            text::ellipsize(&group.label(running), usize::from(width).saturating_sub(2)),
            label_style,
        ),
    ]));
}

pub(super) fn prefix_group_body(lines: &mut LinkedLines, group_last: bool) {
    let last = lines.len().saturating_sub(1);
    lines.prefix(|index| prefix(group_last && index == last, theme::muted_style()));
}

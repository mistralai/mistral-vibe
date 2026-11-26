//! A turn's `Worked for` group: its header, and once unfolded, its steps behind a rail like a tool group's.

use ratatui::text::{Line, Span};

use super::bordered::prefix;
use super::{expand_marker, RenderedEntry};
use crate::transcript::TurnDetails;
use crate::ui::markdown::{self, LinkedLines};
use crate::ui::theme;
use crate::utils::text;

/// Columns the rail of an unfolded group indents its steps by.
pub(in crate::ui::transcript) const INDENT: u16 = 2;

/// A step's place in its turn's `Worked for` group.
#[derive(Clone, Copy)]
pub(in crate::ui::transcript) struct DetailsView<'a> {
    pub details: &'a TurnDetails,
    /// The group shows its steps.
    pub expanded: bool,
}

impl DetailsView<'_> {
    pub(in crate::ui::transcript) fn first(&self, index: usize) -> bool {
        index == self.details.first
    }

    pub(in crate::ui::transcript) fn last(&self, index: usize) -> bool {
        index == self.details.close
    }
}

/// A step behind the group's rail, rendered by `step` at the inner width; folded, only the header shows.
pub(super) fn render(
    index: usize,
    view: DetailsView<'_>,
    width: u16,
    step: impl FnOnce(u16) -> RenderedEntry,
) -> RenderedEntry {
    let (first, last) = (view.first(index), view.last(index));
    let mut lines = LinkedLines::default();
    if first {
        lines.push_gap();
        push_header(&mut lines, view, width);
    }
    if !view.expanded {
        return RenderedEntry::owned(lines);
    }
    let step = step(width.saturating_sub(INDENT));
    let top = lines.len() as u16;
    let mut cells = step.table_cells().to_vec();
    cells.iter_mut().for_each(|cell| shift(cell, top));
    let mut body = step.into_linked();
    let end = body.len().saturating_sub(1);
    body.prefix(|row| prefix(last && row == end, expanded_style()));
    lines.append(body);
    // A blank row sets the steps apart from the final answer.
    if last {
        lines.push_gap();
    }
    RenderedEntry::owned(lines).with_cells(cells)
}

/// Move a step's table `cell` right by the indent and down by the `rows` the group's header adds.
fn shift(cell: &mut markdown::TableCell, rows: u16) {
    cell.area.x += INDENT;
    cell.area.y += rows;
    for span in &mut cell.spans {
        (span.x0, span.x1, span.y) = (span.x0 + INDENT, span.x1 + INDENT, span.y + rows);
    }
}

/// An unfolded group's header and the rail along its steps: the primary tint.
fn expanded_style() -> ratatui::style::Style {
    theme::text(theme::text_primary())
}

/// `⏵ Worked for 1m 12s · 2 messages · 3 tool calls` on one row, primary once unfolded.
fn push_header(lines: &mut LinkedLines, view: DetailsView<'_>, width: u16) {
    let style = match view.expanded {
        true => expanded_style(),
        false => theme::dim(theme::effect_message()),
    };
    let label = text::ellipsize(&view.details.label(), usize::from(width).saturating_sub(2));
    lines.push(Line::from(vec![
        Span::styled(format!("{} ", expand_marker(view.expanded)), style),
        Span::styled(label, style),
    ]));
}

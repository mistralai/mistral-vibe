//! An entry's rendered rows: owned lines, or prepared Markdown shared with the cache.

use std::sync::Arc;

use ratatui::text::Line;

use crate::selection::Fold;
use crate::ui::markdown::{self, LinkedLines};

pub(in crate::ui::transcript) struct RenderedEntry {
    lines: LinkedLines,
    prepared: Option<Arc<markdown::PreparedMarkdown>>,
    table_cells: bool,
    /// Table cells of owned lines, such as a step's under its turn's `Worked for` header.
    cells: Vec<markdown::TableCell>,
}

pub(in crate::ui::transcript) struct RenderedParts {
    pub lines: LinkedLines,
    pub prepared: Option<Arc<markdown::PreparedMarkdown>>,
}

impl RenderedEntry {
    pub(super) fn owned(lines: LinkedLines) -> Self {
        Self {
            lines,
            prepared: None,
            table_cells: false,
            cells: Vec::new(),
        }
    }

    pub(super) fn prepared(prepared: Arc<markdown::PreparedMarkdown>, table_cells: bool) -> Self {
        Self {
            lines: LinkedLines::default(),
            prepared: Some(prepared),
            table_cells,
            cells: Vec::new(),
        }
    }

    /// Owned lines with the table cells they paint.
    pub(super) fn with_cells(mut self, cells: Vec<markdown::TableCell>) -> Self {
        self.cells = cells;
        self
    }

    /// The rows as owned lines, copying prepared markdown out of its cache.
    pub(super) fn into_linked(self) -> LinkedLines {
        match self.prepared {
            Some(prepared) => prepared.linked().clone(),
            None => self.lines,
        }
    }

    pub(super) fn prepend(&mut self, mut prefix: LinkedLines) {
        match self.prepared.take() {
            Some(prepared) => prefix.append(prepared.linked().clone()),
            None => prefix.append(std::mem::take(&mut self.lines)),
        }
        self.lines = prefix;
    }

    pub(in crate::ui::transcript) fn lines(&self) -> &[Line<'static>] {
        self.prepared
            .as_ref()
            .map_or(self.lines.lines(), |prepared| prepared.lines())
    }

    pub(in crate::ui::transcript) fn folds(&self) -> &[Option<Fold>] {
        self.prepared
            .as_ref()
            .map_or(self.lines.folds(), |prepared| prepared.linked().folds())
    }

    /// Rows above the header a click toggles: an ungrouped entry's leading gaps.
    pub(in crate::ui::transcript) fn header_offset(&self, grouped: bool) -> u16 {
        let gaps = self.gaps().iter().enumerate();
        (!grouped as usize * gaps.take_while(|(row, gap)| row == *gap).count()) as u16
    }

    pub(in crate::ui::transcript) fn gaps(&self) -> &[usize] {
        self.prepared
            .as_ref()
            .map_or(self.lines.gaps(), |prepared| prepared.linked().gaps())
    }

    pub(in crate::ui::transcript) fn prewrapped(&self, width: u16) -> bool {
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

    pub(in crate::ui::transcript) fn table_cells(&self) -> &[markdown::TableCell] {
        match (&self.prepared, self.table_cells) {
            (Some(prepared), true) => prepared.cells(),
            _ => &self.cells,
        }
    }

    pub(in crate::ui::transcript) fn into_parts(self) -> RenderedParts {
        RenderedParts {
            lines: self.lines,
            prepared: self.prepared,
        }
    }
}

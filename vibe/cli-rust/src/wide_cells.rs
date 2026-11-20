//! Repaint columns a narrower cell uncovers, which terminals erase with that cell's style.

use ratatui::buffer::{Buffer, CellDiffOption, CellWidth};

/// Wide cells `(index, width)` of the last drawn frame.
#[derive(Default)]
pub struct WideCells(Vec<(usize, u16)>);

impl WideCells {
    /// Force writes on the columns a narrower cell uncovers, then remember this frame's wide cells.
    pub fn refresh(&mut self, buffer: &mut Buffer) {
        let content = &mut buffer.content;
        for &(index, width) in &self.0 {
            if content
                .get(index)
                .is_none_or(|cell| cell.cell_width() >= width)
            {
                continue;
            }
            let end = (index + usize::from(width)).min(content.len());
            for cell in content.get_mut(index + 1..end).unwrap_or_default() {
                if cell.diff_option == CellDiffOption::None {
                    cell.set_diff_option(CellDiffOption::AlwaysUpdate);
                }
            }
        }
        self.0.clear();
        self.0.extend(
            content
                .iter()
                .enumerate()
                .map(|(index, cell)| (index, cell.cell_width()))
                .filter(|&(_, width)| width > 1),
        );
    }
}

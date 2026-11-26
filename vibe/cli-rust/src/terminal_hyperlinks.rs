//! OSC 8 hyperlinks on clickable link cells, so the terminal can open links itself.

use std::num::NonZeroU16;

use ratatui::buffer::{Buffer, CellDiffOption, CellWidth};

use crate::app::App;
use crate::mouse::{self, MouseTarget};
use crate::ui::markdown::{Link, LinkKind};

// Destinations repeat in every linked cell: oversized URLs stay plain text.
const MAX_DESTINATION_BYTES: usize = 8 * 1024;
// An autolink's label is its URL, so its escapes grow with the URL's square: links whose visible cells exceed this stay plain.
const MAX_LINK_BYTES: usize = 256 * 1024;

/// Wrap every cell of an external web link a click on `surface` would open; run after the frame is painted.
pub fn mark(app: &App, buffer: &mut Buffer, surface: MouseTarget) {
    for link in &app.view.link_hitmap {
        if !linkable(link) {
            continue;
        }
        for &(y, x, width) in link.rows() {
            let mut covered = 0;
            for x in x..x.saturating_add(width) {
                if covered > 0 {
                    covered -= 1;
                    continue;
                }
                if mouse::target_at(app, (x, y)) != Some(surface) {
                    continue;
                }
                let Some(cell) = buffer.cell_mut((x, y)) else {
                    continue;
                };
                if cell.diff_option != CellDiffOption::None {
                    continue;
                }
                let cell_width = cell.cell_width().max(1);
                covered = cell_width - 1;
                let symbol = format!("\x1b]8;;{}\x07{}\x1b]8;;\x07", link.target(), cell.symbol());
                cell.set_symbol(&symbol)
                    .set_diff_option(CellDiffOption::ForcedWidth(
                        NonZeroU16::new(cell_width).unwrap_or(NonZeroU16::MIN),
                    ));
            }
        }
    }
}

fn linkable(link: &Link) -> bool {
    let target = link.target();
    let cells: usize = link
        .rows()
        .iter()
        .map(|&(_, _, width)| width as usize)
        .sum();
    link.kind() == LinkKind::External
        && target.len() <= MAX_DESTINATION_BYTES
        && target.len().saturating_mul(cells) <= MAX_LINK_BYTES
        && crate::external_url::is_safe(target)
}

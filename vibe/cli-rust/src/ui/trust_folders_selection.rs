//! Resolve trust selections against source text rather than visual line breaks.

use std::sync::Arc;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use super::trust_folders_layout::{build, scroll_content, CONTENT_MAX};
use super::trust_folders_paint::paint_centered_row;
use super::trust_folders_text::Row;
use crate::app::App;
use crate::selection::region::RowSpan;
use crate::selection::ScrollTarget;

pub(crate) struct SelectionSlice {
    pub buffer: Buffer,
    pub area: Rect,
    rows: Vec<Row>,
    scroll_rows: usize,
    scroll_width: usize,
}

impl SelectionSlice {
    fn width(&self, index: usize) -> usize {
        if index < self.scroll_rows {
            self.scroll_width
        } else {
            usize::from(self.area.width)
        }
    }

    pub fn extract(&self, spans: &[RowSpan]) -> String {
        let mut text = String::new();
        let mut previous: Option<(&Arc<str>, usize)> = None;
        for &(y, x0, x1) in spans {
            let index = usize::from(y - self.area.y);
            let row = &self.rows[index];
            let left = row.left(self.area.x, self.width(index));
            if x1 < left {
                continue;
            }
            for source in &row.sources {
                let Some(range) =
                    source.selected(usize::from(x0.saturating_sub(left)), usize::from(x1 - left))
                else {
                    continue;
                };
                let start = match previous {
                    Some((prior, end)) if Arc::ptr_eq(prior, &source.text) => end,
                    _ => {
                        if !text.is_empty() {
                            text.push('\n');
                        }
                        range.start
                    }
                };
                text.push_str(&source.text[start..range.end]);
                previous = Some((&source.text, range.end));
            }
        }
        text
    }
}

pub(crate) fn selection_slice(app: &App) -> Option<SelectionSlice> {
    app.trust.details.as_ref()?;
    let selection = app.selection.region.as_ref()?;
    let region = app.view.selection_region;
    let (rows, width, scroll_rows, scroll_width) = if selection.scroll_target == ScrollTarget::Trust
    {
        let width = region.content().width;
        let rows = scroll_content(&app.trust, usize::from(width));
        (rows, width, 0, 0)
    } else {
        let viewport = if region.scrollbar {
            usize::from(region.scroll_area.height)
        } else {
            CONTENT_MAX
        };
        let layout = build(&app.trust, usize::from(region.area.width), viewport);
        let mut rows = layout.rows;
        rows.truncate(usize::from(region.area.height.saturating_sub(2)));
        (
            rows,
            region.area.width,
            layout.scroll_rows,
            layout.scroll_width,
        )
    };
    let area = Rect::new(region.area.x, 1, width, u16::try_from(rows.len()).ok()?);
    let mut slice = SelectionSlice {
        buffer: Buffer::empty(area),
        area,
        rows,
        scroll_rows,
        scroll_width,
    };
    for (index, row) in slice.rows.iter().enumerate() {
        let width = slice.width(index);
        paint_centered_row(&mut slice.buffer, row, area.x, width, area.y + index as u16);
    }
    Some(slice)
}

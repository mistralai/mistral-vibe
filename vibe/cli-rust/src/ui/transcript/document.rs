//! Render the selected transcript rows outside the viewport for clipboard extraction.

use std::ops::Range;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Widget, Wrap};

use super::viewport::{document_height, measure};
use super::{entry, paint};
use crate::app::App;
use crate::selection::{fold, Fold, Folds};

pub(crate) struct SelectionSlice {
    pub buffer: Buffer,
    pub area: Rect,
    pub gutters: Vec<(u16, u16, u16)>,
    pub folds: Folds,
    /// Sorted spacing rows (Textual margins) a copy skips.
    pub margins: Vec<u16>,
}

/// Lines painted as one block, with their folds and the indexes of their spacing rows.
struct Painted<'a> {
    lines: &'a [Line<'static>],
    folds: &'a [Option<Fold>],
    gaps: &'a [usize],
}

pub(crate) fn selection_slice(app: &App) -> Option<SelectionSlice> {
    let selection = app.selection.region.as_ref()?;
    let view = &app.view;
    let width = view.selection_region.content().width;
    if width == 0 {
        return None;
    }

    // The child view's selection must extract the child's document: the
    // render swap is over by the time the overlay runs.
    let (transcript, cache) = crate::subagents::active_transcript(app);
    let config = view.banner.view(&app.session.startup_config);
    let banner = super::banner(app, config, width, transcript.is_empty());
    let layout = cache.layout(transcript.revision(), width)?;
    let spacer = view.queue_spacer;
    let total = document_height(banner.lines(), layout, width).saturating_add(spacer.height);
    let start = selection.anchor.1.min(selection.head.1).max(0) as u16;
    let end = selection
        .anchor
        .1
        .max(selection.head.1)
        .clamp(0, i32::from(total.saturating_sub(1))) as u16;
    if total == 0 || start >= total || start > end {
        return None;
    }

    let area = Rect::new(
        view.selection_region.area.x,
        start,
        width,
        end.saturating_sub(start).saturating_add(1),
    );
    let mut buffer = Buffer::empty(area);
    let mut gutters = Vec::new();
    let mut folds = Folds {
        x: area.x,
        rows: Vec::new(),
    };
    let mut margins = Vec::new();
    let banner_height = measure(banner.lines(), width);
    let painted = Painted {
        lines: banner.lines(),
        folds: banner.folds(),
        gaps: banner.gaps(),
    };
    let visible = area.y..area.bottom();
    push_rows(
        &mut folds.rows,
        &mut margins,
        0,
        painted,
        width,
        false,
        visible.clone(),
    );
    render_segment(&mut buffer, area, 0, banner_height, banner.lines(), false);

    let entries_top = banner_height.saturating_add(u16::from(!layout.entries.is_empty()));
    margins.extend(banner_height..entries_top);
    let spacer_top = entries_top.saturating_add(spacer.at);
    margins.extend(spacer_top..spacer_top.saturating_add(spacer.height));
    for positioned in &layout.entries {
        let y = entries_top.saturating_add(spacer.offset(positioned.top));
        if y >= area.bottom() {
            break;
        }
        if y.saturating_add(positioned.height) <= area.y {
            continue;
        }
        let Some(value) = transcript.entry(positioned.index) else {
            continue;
        };
        let entry_expanded = view.expanded.contains(value.id);
        let group_expanded = value
            .group
            .as_ref()
            .is_some_and(|group| view.expanded.contains(&group.key));
        if value.group.is_none() || group_expanded {
            if let Some(gutter) = entry::diff_gutter(&value, entry_expanded) {
                gutters.push((y, y.saturating_add(positioned.height), gutter));
            }
        }
        let queue = entry::QueueView {
            selected: app.queue.selected.as_deref() == Some(value.id),
            paused: app.queue.paused,
        };
        let rendered = entry::render(
            &value,
            width,
            entry::ExpansionView {
                entry: entry_expanded,
                group: group_expanded,
            },
            view.pulse_frame,
            queue,
            app.rewind.entry_id.as_deref() == Some(value.id),
            None,
        );
        let lines = rendered.lines();
        let prewrapped = positioned.prewrapped;
        let painted = Painted {
            lines,
            folds: rendered.folds(),
            gaps: rendered.gaps(),
        };
        let rows = &mut folds.rows;
        push_rows(
            rows,
            &mut margins,
            y,
            painted,
            width,
            prewrapped,
            visible.clone(),
        );
        render_segment(&mut buffer, area, y, positioned.height, lines, prewrapped);
    }
    margins.sort_unstable();
    Some(SelectionSlice {
        buffer,
        area,
        gutters,
        folds,
        margins,
    })
}

/// Cut each painted row's hang indent out of the highlight, as the copy drops it.
pub(super) fn hang_cells(
    hangs: &mut Vec<(u16, u16, u16)>,
    area: Rect,
    entry_y: i32,
    rendered: &entry::RenderedEntry,
    prewrapped: bool,
) {
    let mut y = entry_y;
    for (index, line) in rendered.lines().iter().enumerate() {
        if y >= i32::from(area.bottom()) {
            break;
        }
        let fold = rendered.folds().get(index).copied().flatten();
        if let Some(fold) = fold.filter(|fold| fold.hang > 0 && y >= i32::from(area.y)) {
            hangs.push((y as u16, area.x, area.x + fold.hang - 1));
        }
        y += i32::from(if prewrapped {
            1
        } else {
            line_rows(line, area.width)
        });
    }
}

/// Record the fold and margin rows of `painted`, laid out from row `top`, through the `visible` rows.
fn push_rows(
    rows: &mut Vec<(u16, Fold)>,
    margins: &mut Vec<u16>,
    top: u16,
    painted: Painted<'_>,
    width: u16,
    prewrapped: bool,
    visible: Range<u16>,
) {
    let Painted { lines, folds, gaps } = painted;
    let mut y = top;
    for (index, line) in lines.iter().enumerate() {
        if y >= visible.end {
            break;
        }
        if let Some(fold) = folds.get(index).copied().flatten() {
            rows.push((y, fold));
        }
        // An unwrapped line takes the rows `Paragraph` paints, two for a whitespace-only one.
        let height = match prewrapped {
            true => 1,
            false => line_rows(line, width),
        };
        let bottom = y.saturating_add(height);
        if gaps.binary_search(&index).is_ok() {
            margins.extend(y..bottom);
        }
        if height > 1 && bottom > visible.start {
            let painted = wrapped_rows(line, width, height);
            let source: String = line
                .spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect();
            for (index, gap) in fold::gaps(&source, &painted).into_iter().enumerate() {
                if let Some(fold) = Fold::hung(gap, 0) {
                    rows.push((y.saturating_add(index as u16), fold));
                }
            }
        }
        y = bottom;
    }
}

fn line_rows(line: &Line<'static>, width: u16) -> u16 {
    let paragraph = Paragraph::new(line.clone()).wrap(Wrap { trim: false });
    paragraph.line_count(width).max(1) as u16
}

/// The text of each of the `height` rows `Paragraph` wraps one line into.
fn wrapped_rows(line: &Line<'static>, width: u16, height: u16) -> Vec<String> {
    let paragraph = Paragraph::new(line.clone()).wrap(Wrap { trim: false });
    let area = Rect::new(0, 0, width, height);
    let mut buffer = Buffer::empty(area);
    paragraph.render(area, &mut buffer);
    (0..height)
        .map(|y| fold::cells(&buffer, 0, width.saturating_sub(1), y))
        .collect()
}

/// Paint `lines` from document row `y` the way the viewport does, so the copy reads the rows it shows.
fn render_segment(
    buffer: &mut Buffer,
    area: Rect,
    y: u16,
    height: u16,
    lines: &[Line<'static>],
    prewrapped: bool,
) {
    let bottom = y.saturating_add(height);
    if height == 0 || bottom <= area.y || y >= area.bottom() {
        return;
    }
    let offset = area.y.saturating_sub(y);
    let top = y.max(area.y);
    let height = height
        .saturating_sub(offset)
        .min(area.bottom().saturating_sub(top));
    let rect = Rect::new(area.x, top, area.width, height);
    if prewrapped {
        paint::paint_prewrapped(buffer, rect, offset, lines);
        return;
    }
    Paragraph::new(lines.to_vec())
        .wrap(Wrap { trim: false })
        .scroll((offset, 0))
        .render(rect, buffer);
}

#[cfg(test)]
mod tests;

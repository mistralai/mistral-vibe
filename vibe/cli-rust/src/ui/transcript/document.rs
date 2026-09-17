//! Render the selected transcript rows outside the viewport for clipboard extraction.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Widget, Wrap};

use super::entry;
use super::viewport::{document_height, measure};
use crate::app::App;

pub(crate) struct SelectionSlice {
    pub buffer: Buffer,
    pub area: Rect,
    pub gutters: Vec<(u16, u16, u16)>,
}

pub(crate) fn selection_slice(app: &App) -> Option<SelectionSlice> {
    let selection = app.selection.region.as_ref()?;
    let view = &app.view;
    let width = view.selection_region.content().width;
    if width == 0 {
        return None;
    }

    let banner = view.banner.view(&app.session.startup_config);
    let layout = view
        .transcript_cache
        .layout(view.transcript.revision(), width)?;
    let total = document_height(&banner, layout, width);
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
    let banner_height = measure(&banner, width);
    render_segment(&mut buffer, area, 0, banner_height, banner);

    let entries_top = banner_height.saturating_add(u16::from(!layout.entries.is_empty()));
    for positioned in &layout.entries {
        let y = entries_top.saturating_add(positioned.top);
        if y >= area.bottom() {
            break;
        }
        if y.saturating_add(positioned.height) <= area.y {
            continue;
        }
        let Some(value) = view.transcript.entry(positioned.index) else {
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
        render_segment(
            &mut buffer,
            area,
            y,
            positioned.height,
            rendered.lines().to_vec(),
        );
    }
    Some(SelectionSlice {
        buffer,
        area,
        gutters,
    })
}

fn render_segment(buffer: &mut Buffer, area: Rect, y: u16, height: u16, lines: Vec<Line<'static>>) {
    let bottom = y.saturating_add(height);
    if height == 0 || bottom <= area.y || y >= area.bottom() {
        return;
    }
    let offset = area.y.saturating_sub(y);
    let top = y.max(area.y);
    let height = height
        .saturating_sub(offset)
        .min(area.bottom().saturating_sub(top));
    Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .scroll((offset, 0))
        .render(Rect::new(area.x, top, area.width, height), buffer);
}

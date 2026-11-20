//! Viewport-cull and paint transcript entries plus their hit maps.

use std::collections::HashSet;

use ratatui::layout::Rect;
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::{buffer::Buffer, Frame};

use super::{entry, paint, table_cells};

mod hits;

use crate::selection::{RowChrome, TableCellHit, TableCellKey};
use crate::transcript::Transcript;
use crate::ui::markdown;
use crate::utils::transcript_cache::{QueueSpacer, TranscriptLayout};
pub(super) use hits::push_chrome;

pub(super) fn document_height(
    banner: &[Line<'static>],
    layout: &TranscriptLayout,
    width: u16,
) -> u16 {
    let gap = u16::from(!layout.entries.is_empty());
    measure(banner, width)
        .saturating_add(gap)
        .saturating_add(layout.height)
}

pub(super) struct Hitmaps<'a> {
    pub entries: &'a mut Vec<(u16, u16, Option<u16>, String)>,
    pub links: &'a mut Vec<markdown::Link>,
    /// Border and edit-diff gutter columns of each painted entry's rows.
    pub chrome: &'a mut Vec<RowChrome>,
    /// `[top, bottom)` screen rows of `/raw` agent messages.
    pub raws: &'a mut Vec<(u16, u16)>,
    pub tables: &'a mut Vec<TableCellHit>,
    /// `(index, top, height)` of each painted entry, `top` relative to the viewport top.
    pub rows: &'a mut Vec<(usize, i32, u16)>,
    /// The banner cat's painted area, for its click-to-replay gesture.
    pub cat: &'a mut Option<Rect>,
    /// Hang indent cells cut out of the selection highlight, filled only while selecting.
    pub hangs: Option<&'a mut Vec<(u16, u16, u16)>>,
}

pub(super) struct Viewport<'a> {
    pub layout: &'a TranscriptLayout,
    pub area: Rect,
    pub width: u16,
    pub top: i32,
    /// The startup banner with the promo under it, and the promo's links.
    pub banner: &'a markdown::LinkedLines,
    pub spacer: QueueSpacer,
    pub pulse_frame: usize,
    pub selected: Option<&'a str>,
    pub selected_table: Option<&'a (TableCellKey, usize)>,
    pub paused: bool,
    pub queue_hints: &'static [crate::hints::Hint],
    /// `/raw`: agent messages show their Markdown source.
    pub raw: bool,
    pub rewind: Option<&'a str>,
}

pub(super) fn render(
    expanded: &HashSet<String>,
    markdown_cache: &mut markdown::MarkdownCache,
    hitmaps: Hitmaps<'_>,
    mouse_position: Option<(u16, u16)>,
    transcript: &Transcript,
    frame: &mut Frame,
    viewport: Viewport<'_>,
) {
    let Hitmaps {
        entries: hitmap,
        links,
        chrome,
        raws,
        tables,
        rows,
        cat,
        mut hangs,
    } = hitmaps;
    let Viewport {
        layout,
        area,
        width,
        mut top,
        banner,
        spacer,
        pulse_frame,
        selected,
        selected_table,
        paused,
        queue_hints,
        raw,
        rewind,
    } = viewport;
    let viewport_bottom = area.y as i32 + area.height as i32;
    let document_top = top;
    if let Some(hangs) = hangs.as_deref_mut() {
        super::document::hang_cells(hangs, area, top, (banner.lines(), banner.folds()), false);
    }
    top = place(
        frame,
        area,
        top,
        measure(banner.lines(), width),
        banner.lines(),
        false,
        |buffer, rect, offset| {
            *cat = crate::ui::banner::cat_click_area(rect, offset);
            let promo = markdown::screen_links(banner, false, rect, offset);
            hover(buffer, &promo, mouse_position);
            links.extend(promo);
        },
    );
    if layout.entries.is_empty() {
        return;
    }
    top = place(frame, area, top, 1, &[Line::from("")], false, |_, _, _| {});
    let viewport_top = area.y as i32;
    let visible_document_top =
        u16::try_from((viewport_top - document_top).max(0)).unwrap_or(u16::MAX);
    let visible_document_bottom =
        u16::try_from((viewport_bottom - document_top).max(0)).unwrap_or(u16::MAX);
    let hidden_height = (viewport_top - top).max(0) as u16;
    let first = layout
        .entries
        .partition_point(|entry| entry.top.saturating_add(entry.height) <= hidden_height);
    for positioned in layout.entries[first..]
        .iter()
        .filter(|entry| entry.height > 0)
    {
        let entry_y = top + i32::from(spacer.offset(positioned.top));
        if entry_y >= viewport_bottom {
            break;
        }
        let Some(value) = transcript.entry(positioned.index) else {
            continue;
        };
        let details = layout.details_of(positioned.index);
        let expansion = entry::ExpansionView::of(expanded, &value, details, raw);
        let (entry_expanded, content_visible) =
            (expansion.entry, expansion.content_visible(&value));
        let rendered = entry::render(
            &value,
            width,
            expansion,
            pulse_frame,
            entry::QueueView {
                selected: selected == Some(value.id),
                paused,
                hints: queue_hints,
            },
            rewind == Some(value.id),
            Some(markdown_cache),
        );
        if let Some(hangs) = hangs.as_deref_mut().filter(|_| content_visible) {
            let painted = (rendered.lines(), rendered.folds());
            super::document::hang_cells(hangs, area, entry_y, painted, positioned.prewrapped);
        }
        let bounds = viewport_top..viewport_bottom;
        let document_y = (entry_y - document_top) as u16;
        if content_visible {
            tables.extend(table_cells::collect(
                &value,
                rendered.table_cells(),
                document_y,
                area.x,
                visible_document_top,
                visible_document_bottom,
                selected_table,
            ));
        }
        let visible_top = entry_y.max(viewport_top);
        let visible_bottom = (entry_y + positioned.height as i32).min(viewport_bottom);
        if visible_bottom > visible_top {
            rows.push((positioned.index, entry_y - viewport_top, positioned.height));
            let details_header = details.filter(|group| group.first == positioned.index);
            let entry_y =
                hits::push_header(hitmap, entry_y, &bounds, details_header.map(|g| &g.key));
            let rail_top = entry_y.max(viewport_top);
            // A folded `Worked for` group paints none of its steps, tool group headers included.
            let shown = expansion.details.is_none_or(|step| step.expanded);
            let group_header = (value.group.as_ref()).filter(|group| group.first && shown);
            let content_top =
                hits::push_header(hitmap, entry_y, &bounds, group_header.map(|g| &g.key));
            let header = content_top + i32::from(rendered.header_offset(value.group.is_some()));
            let header = (viewport_top..viewport_bottom)
                .contains(&header)
                .then_some(header as u16);
            let content_top = content_top.max(viewport_top);
            if content_visible && visible_bottom > content_top {
                hitmap.push((
                    content_top as u16,
                    visible_bottom as u16,
                    header,
                    value.id.to_owned(),
                ));
                if entry::is_raw(&value, raw) {
                    raws.push((content_top as u16, visible_bottom as u16));
                }
            }
            let railed = expansion.details.is_some_and(|step| step.expanded);
            let own = content_visible.then(|| entry::chrome(&value, entry_expanded));
            let rows = (rail_top, content_top, visible_bottom);
            hits::push_chrome(chrome, rows, railed, own.unwrap_or_default());
        }
        paint::entry(
            frame,
            area,
            entry_y,
            positioned.height,
            rendered,
            positioned.prewrapped,
            |buffer, entry_links| {
                if !content_visible {
                    return;
                }
                hover(buffer, &entry_links, mouse_position);
                links.extend(entry_links);
            },
        );
    }
}

/// Paint the hover style on the links under the pointer.
fn hover(buffer: &mut Buffer, links: &[markdown::Link], mouse_position: Option<(u16, u16)>) {
    let Some(position) = mouse_position else {
        return;
    };
    for link in links.iter().filter(|link| link.contains(position)) {
        link.paint_hover(buffer);
    }
}

fn place(
    frame: &mut Frame,
    area: Rect,
    y: i32,
    height: u16,
    lines: &[Line<'static>],
    prewrapped: bool,
    decorate: impl FnOnce(&mut Buffer, Rect, u16),
) -> i32 {
    let bottom = y + height as i32;
    let viewport_top = area.y as i32;
    let viewport_bottom = viewport_top + area.height as i32;
    if bottom > viewport_top && y < viewport_bottom && height > 0 {
        let offset = (viewport_top - y).max(0) as u16;
        let screen_y = y.max(viewport_top);
        let available = (viewport_bottom - screen_y) as u16;
        let height = (height - offset).min(available);
        if height > 0 {
            let rect = Rect {
                x: area.x,
                y: screen_y as u16,
                width: area.width,
                height,
            };
            paint::report_overflow(lines, area.width);
            if prewrapped {
                let buffer = frame.buffer_mut();
                for (row, line) in lines
                    .iter()
                    .skip(offset as usize)
                    .take(height as usize)
                    .enumerate()
                {
                    buffer.set_line(rect.x, rect.y + row as u16, line, area.width);
                }
            } else {
                let paragraph = Paragraph::new(lines.to_vec())
                    .wrap(Wrap { trim: false })
                    .scroll((offset, 0));
                frame.render_widget(paragraph, rect);
            }
            decorate(frame.buffer_mut(), rect, offset);
        }
    }
    bottom
}

fn fits(lines: &[Line<'static>], width: u16) -> bool {
    width > 0 && lines.iter().all(|line| line.width() <= width as usize)
}

pub(super) fn measure(lines: &[Line<'static>], width: u16) -> u16 {
    measure_known(lines, width, fits(lines, width))
}

pub(super) fn measure_known(lines: &[Line<'static>], width: u16, prewrapped: bool) -> u16 {
    if prewrapped {
        return lines.len() as u16;
    }
    Paragraph::new(lines.to_vec())
        .wrap(Wrap { trim: false })
        .line_count(width) as u16
}

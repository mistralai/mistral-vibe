//! Viewport-cull and paint transcript entries plus their hit maps.

use std::collections::HashSet;

use ratatui::layout::Rect;
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::{buffer::Buffer, Frame};

use super::{entry, paint, table_cells};
use crate::selection::{TableCellHit, TableCellKey};
use crate::transcript::Transcript;
use crate::ui::markdown;
use crate::utils::transcript_cache::TranscriptLayout;

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
    pub entries: &'a mut Vec<(u16, u16, String)>,
    pub links: &'a mut Vec<markdown::Link>,
    pub diffs: &'a mut Vec<(u16, u16, u16)>,
    pub tables: &'a mut Vec<TableCellHit>,
}

pub(super) struct Viewport<'a> {
    pub layout: &'a TranscriptLayout,
    pub area: Rect,
    pub width: u16,
    pub top: i32,
    pub banner: &'a [Line<'static>],
    /// `(label, url)` pairs the promo under the banner declares.
    pub promo_targets: &'a [(String, String)],
    pub pulse_frame: usize,
    pub selected: Option<&'a str>,
    pub selected_table: Option<&'a (TableCellKey, usize)>,
    pub paused: bool,
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
        diffs,
        tables,
    } = hitmaps;
    let Viewport {
        layout,
        area,
        width,
        mut top,
        banner,
        promo_targets,
        pulse_frame,
        selected,
        selected_table,
        paused,
        rewind,
    } = viewport;
    let viewport_bottom = area.y as i32 + area.height as i32;
    let document_top = top;
    top = place(
        frame,
        area,
        top,
        measure(banner, width),
        banner,
        false,
        |buffer, rect| {
            if promo_targets.is_empty() {
                return;
            }
            let promo_links =
                markdown::links(buffer, rect, promo_targets, markdown::LinkKind::External);
            if let Some(position) = mouse_position {
                for link in &promo_links {
                    if link.contains(position) {
                        link.paint_hover(buffer, position);
                    }
                }
            }
            links.extend(promo_links);
        },
    );
    if layout.entries.is_empty() {
        return;
    }
    top = place(frame, area, top, 1, &[Line::from("")], false, |_, _| {});
    let viewport_top = area.y as i32;
    let visible_document_top =
        u16::try_from((viewport_top - document_top).max(0)).unwrap_or(u16::MAX);
    let visible_document_bottom =
        u16::try_from((viewport_bottom - document_top).max(0)).unwrap_or(u16::MAX);
    let hidden_height = (viewport_top - top).max(0) as u16;
    let first = layout
        .entries
        .partition_point(|entry| entry.top.saturating_add(entry.height) <= hidden_height);
    for positioned in &layout.entries[first..] {
        let entry_y = top + positioned.top as i32;
        if entry_y >= viewport_bottom {
            break;
        }
        let Some(value) = transcript.entry(positioned.index) else {
            continue;
        };
        let entry_expanded = expanded.contains(value.id);
        let group_expanded = value
            .group
            .as_ref()
            .is_some_and(|group| expanded.contains(&group.key));
        let content_visible = value.group.is_none() || group_expanded;
        let rendered = entry::render(
            &value,
            width,
            entry::ExpansionView {
                entry: entry_expanded,
                group: group_expanded,
            },
            pulse_frame,
            entry::QueueView {
                selected: selected == Some(value.id),
                paused,
            },
            rewind == Some(value.id),
            Some(markdown_cache),
        );
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
            let content_top = if let Some(group) = value.group.as_ref().filter(|group| group.first)
            {
                let header_top = (entry_y + 1).max(viewport_top);
                let header_bottom = (entry_y + 2).min(viewport_bottom);
                if header_bottom > header_top {
                    hitmap.push((header_top as u16, header_bottom as u16, group.key.clone()));
                }
                entry_y + 2
            } else {
                entry_y
            };
            let content_top = content_top.max(viewport_top);
            if content_visible && visible_bottom > content_top {
                hitmap.push((
                    content_top as u16,
                    visible_bottom as u16,
                    value.id.to_owned(),
                ));
                if let Some(gutter) = entry::diff_gutter(&value, entry_expanded) {
                    diffs.push((content_top as u16, visible_bottom as u16, gutter));
                }
            }
        }
        paint::entry(
            frame,
            area,
            entry_y,
            positioned.height,
            rendered,
            positioned.prewrapped,
            |buffer, rect, targets, link_kind| {
                if !content_visible || targets.is_empty() {
                    return;
                }
                let entry_links = markdown::links(buffer, rect, targets, link_kind);
                if let Some(position) = mouse_position {
                    for link in &entry_links {
                        if link.contains(position) {
                            link.paint_hover(buffer, position);
                        }
                    }
                }
                links.extend(entry_links);
            },
        );
    }
}

fn place(
    frame: &mut Frame,
    area: Rect,
    y: i32,
    height: u16,
    lines: &[Line<'static>],
    prewrapped: bool,
    decorate: impl FnOnce(&mut Buffer, Rect),
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
            report_overflow(lines, area.width);
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
            decorate(frame.buffer_mut(), rect);
        }
    }
    bottom
}

#[cfg(debug_assertions)]
fn report_overflow(lines: &[Line<'static>], width: u16) {
    for line in lines {
        let row_width = line.width();
        if row_width > width as usize {
            tracing::warn!(row_width, width, row = %line, "transcript row overflows its width");
        }
    }
}

#[cfg(not(debug_assertions))]
fn report_overflow(_lines: &[Line<'static>], _width: u16) {}

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

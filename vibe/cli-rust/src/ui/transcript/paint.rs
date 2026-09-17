//! Paint prepared transcript entries without cloning owned text.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

use super::entry::{RenderedEntry, RenderedParts};

pub(super) fn entry(
    frame: &mut Frame,
    area: Rect,
    y: i32,
    height: u16,
    rendered: RenderedEntry,
    prewrapped: bool,
    decorate: impl FnOnce(&mut Buffer, Rect, &[(String, String)], crate::ui::markdown::LinkKind),
) -> i32 {
    let bottom = y + height as i32;
    let viewport_top = area.y as i32;
    let viewport_bottom = viewport_top + area.height as i32;
    if bottom <= viewport_top || y >= viewport_bottom || height == 0 {
        return bottom;
    }

    let offset = (viewport_top - y).max(0) as u16;
    let screen_y = y.max(viewport_top);
    let available = (viewport_bottom - screen_y) as u16;
    let rect = Rect {
        x: area.x,
        y: screen_y as u16,
        width: area.width,
        height: (height - offset).min(available),
    };
    let RenderedParts {
        lines,
        prepared,
        links,
        link_kind,
    } = rendered.into_parts();
    match prepared {
        Some(prepared) => {
            report_overflow(prepared.lines(), area.width);
            if prewrapped {
                paint_prewrapped(frame.buffer_mut(), rect, offset, prepared.lines());
            } else {
                let paragraph = Paragraph::new(prepared.lines().to_vec())
                    .wrap(Wrap { trim: false })
                    .scroll((offset, 0));
                frame.render_widget(paragraph, rect);
            }
            decorate(
                frame.buffer_mut(),
                rect,
                prepared.links(),
                crate::ui::markdown::LinkKind::External,
            );
        }
        None => {
            report_overflow(&lines, area.width);
            if prewrapped {
                paint_prewrapped(frame.buffer_mut(), rect, offset, &lines);
            } else {
                let paragraph = Paragraph::new(lines)
                    .wrap(Wrap { trim: false })
                    .scroll((offset, 0));
                frame.render_widget(paragraph, rect);
            }
            decorate(frame.buffer_mut(), rect, &links, link_kind);
        }
    }
    bottom
}

fn paint_prewrapped(buffer: &mut Buffer, rect: Rect, offset: u16, lines: &[Line<'static>]) {
    for (row, line) in lines
        .iter()
        .skip(offset as usize)
        .take(rect.height as usize)
        .enumerate()
    {
        buffer.set_line(rect.x, rect.y + row as u16, line, rect.width);
    }
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

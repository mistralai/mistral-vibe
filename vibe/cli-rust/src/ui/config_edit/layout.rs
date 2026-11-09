//! Content-sized, terminal-bounded config editor geometry.

use ratatui::layout::{Margin, Rect};
use ratatui::style::Style;
use ratatui::widgets::{Clear, Paragraph, Wrap};
use ratatui::Frame;

use super::super::theme;
use crate::app::App;
use crate::config_edit::{is_multiline, ConfigEdit, MAX_VISIBLE_CHOICES};

pub(super) struct Layout {
    pub modal: Rect,
    pub main: Rect,
    pub side: Rect,
    pub targets: Rect,
    pub help: Rect,
    pub description_height: u16,
}

pub(super) fn paragraph(text: &str) -> Paragraph<'_> {
    Paragraph::new(text).wrap(Wrap { trim: false })
}

pub(in crate::ui) fn draw_too_small(app: &mut App, f: &mut Frame, area: Rect, text: &str) {
    f.render_widget(Clear, area);
    f.render_widget(
        paragraph(text).style(
            Style::default()
                .fg(theme::foreground())
                .bg(theme::surface()),
        ),
        area,
    );
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::Blocked);
}

pub(super) fn height(text: &str, width: u16) -> u16 {
    paragraph(text).line_count(width).min(u16::MAX as usize) as u16
}

impl Layout {
    pub fn new(edit: &ConfigEdit, choices: &[String], area: Rect) -> Option<Self> {
        let width = area.width.saturating_sub(8).min(80);
        if width < 32 {
            return None;
        }
        let inner_width = width - 6;
        let side_width = if edit.field.layers.is_empty() { 0 } else { 37 };
        let multiline = choices.is_empty() && is_multiline(&edit.field);
        let minimum_editor = if choices.is_empty() { 3 } else { 1 };
        let side_by_side_width = inner_width.saturating_sub(side_width);
        let side_by_side_description = if edit.field.description.is_empty() {
            0
        } else {
            height(&edit.field.description, side_by_side_width)
        }
        .saturating_add(u16::from(multiline));
        let stacked = side_width > 0
            && (inner_width < 60
                || side_by_side_description.saturating_add(minimum_editor)
                    > area.height.saturating_sub(10));
        let main_width = if stacked {
            inner_width
        } else {
            side_by_side_width
        };
        let description_height = if edit.field.description.is_empty() {
            0
        } else {
            height(&edit.field.description, main_width)
        }
        .saturating_add(u16::from(multiline));
        let editor_height = if choices.is_empty() {
            if multiline {
                12
            } else {
                3
            }
        } else {
            choices
                .iter()
                .map(|label| usize::from(height(label, main_width - 2)))
                .sum::<usize>()
                .min(MAX_VISIBLE_CHOICES) as u16
        };
        let side_height = if side_width == 0 {
            0
        } else {
            edit.field
                .layers
                .len()
                .saturating_add(2)
                .min(u16::MAX as usize) as u16
        };
        let main_height = description_height.saturating_add(editor_height);
        let desired = if stacked {
            main_height.saturating_add(side_height).saturating_add(1)
        } else {
            main_height.max(side_height)
        };
        let body_height = desired.min(area.height.saturating_sub(10));
        let main_height = if stacked {
            body_height.saturating_sub(side_height.saturating_add(1))
        } else {
            body_height
        };
        if body_height < side_height
            || main_height < description_height.saturating_add(minimum_editor)
        {
            return None;
        }
        let modal = Rect::new(
            area.x + (area.width - width) / 2,
            area.y + (area.height - body_height - 10) / 2,
            width,
            body_height + 10,
        );
        let body = Rect::new(modal.x + 3, modal.y + 2, inner_width, body_height);
        let main = Rect::new(body.x, body.y, main_width, main_height);
        let side = if stacked {
            Rect::new(body.x, main.bottom() + 1, inner_width, side_height)
        } else {
            Rect::new(
                main.right() + u16::from(side_width > 0),
                body.y,
                side_width.saturating_sub(1),
                side_height,
            )
        };
        Some(Self {
            modal,
            main,
            side,
            targets: Rect::new(body.x, body.bottom() + 3, inner_width, 2),
            help: Rect::new(body.x, body.bottom() + 6, inner_width, 1),
            description_height,
        })
    }

    pub fn editor(&self) -> Rect {
        Rect::new(
            self.main.x,
            self.main.y + self.description_height,
            self.main.width,
            self.main.height - self.description_height,
        )
    }
}

pub(super) fn input_inner(area: Rect) -> Rect {
    area.inner(Margin::new(1, 1))
}

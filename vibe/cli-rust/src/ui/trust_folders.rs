//! The workspace-trust gate (Python `trust_folder_dialog.tcss`).

use ratatui::layout::Rect;
use ratatui::widgets::{Block, Borders};
use ratatui::Frame;

use super::theme;
use super::trust_folders_layout::{build, CONTENT_MAX};
use super::trust_folders_paint::paint;
use crate::app::App;
use crate::selection::{Region, ScrollTarget};

/// `#trust-dialog { max-width: 70 }` plus its `border` and `padding: 1 5`.
const MAX_WIDTH: u16 = 70;
pub(super) const PADDING_X: u16 = 5;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut().set_style(area, theme::screen_style());
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::Trust);
    // Nothing is selectable until the dialog below claims its own text column.
    app.view.selection_region = Region::default();
    app.view.selection_chrome.clear();
    app.view.selection_scrollbar.clear();
    if area.height < 3 || area.width == 0 {
        return;
    }
    let border = theme::text(theme::border_blurred());
    let width = area.width as usize;
    let buffer = f.buffer_mut();
    buffer.set_string(area.x, area.y, "▔".repeat(width), border);
    buffer.set_string(area.x, area.bottom() - 1, "▁".repeat(width), border);

    let box_width = MAX_WIDTH.min(area.width);
    let content_width = box_width.saturating_sub(2 + 2 * PADDING_X) as usize;
    // Narrower than the border and padding: only the decorated rows fit.
    if content_width == 0 {
        return;
    }
    // Inline apps keep one decorated row above and below the dialog region.
    let region = Rect::new(area.x, area.y + 1, area.width, area.height - 2);
    let scroll_viewport = usize::from(region.height.saturating_sub(4)).min(CONTENT_MAX);
    let layout = build(&app.trust, content_width, scroll_viewport);
    // Only the draw knows how tall the wrapped content is, so it owns the bounds.
    app.trust.scroll_max = layout.scroll_max;
    app.trust.scroll = layout.scroll;
    let box_height = (layout.rows.len() as u16 + 4).min(region.height);
    let dialog = Rect::new(
        region.x + (region.width - box_width) / 2,
        region.y + (region.height - box_height) / 2,
        box_width,
        box_height,
    );
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_type(ratatui::widgets::BorderType::Rounded)
            .border_style(border),
        dialog,
    );
    let chrome = paint(app, f, dialog, content_width, &layout);
    app.view.selection_chrome = chrome;
    let scroll_area = layout.virtual_rows.and_then(|_| {
        let visible_rows = dialog.height.saturating_sub(4);
        let height = (layout.scroll_rows as u16).min(visible_rows);
        (height > 0).then(|| {
            Rect::new(
                dialog.x + 1 + PADDING_X,
                dialog.y + 2,
                layout.scroll_width as u16,
                height,
            )
        })
    });
    let scroll = i32::try_from(layout.scroll).unwrap_or(i32::MAX);
    // Only the padded text column is selectable; the border and padding are chrome.
    app.view.selection_region = Region {
        area: Rect::new(
            dialog.x + 1 + PADDING_X,
            dialog.y + 1,
            content_width as u16,
            dialog.height.saturating_sub(2),
        ),
        top: i32::from(dialog.y + 1) - scroll,
        scrollbar: layout.virtual_rows.is_some(),
        end_exclusive: true,
        document: false,
        scroll_target: scroll_area.map_or(ScrollTarget::None, |_| ScrollTarget::Trust),
        scroll_area: scroll_area.unwrap_or_default(),
    };
    super::selection::overlay(app, f);
}

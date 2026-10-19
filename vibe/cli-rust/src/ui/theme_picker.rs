//! `/theme` picker bottom-app: a bordered box with a title, the theme option
//! list (live-preview highlight, `›` on the current theme), and a hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{hint_line, list_cursor, scrollbar, theme};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::theme_picker::options;

/// Draw the whole screen with the picker replacing the input box; the chat
/// area shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let kind = super::bottom_app::Kind::Theme;
    super::bottom_app::draw(app, f, area, box_height(area.height), kind, draw_box);
}

/// Visible option rows: at most half the screen height.
fn visible_rows(area_height: u16) -> usize {
    (area_height as usize / 2).min(options().len())
}

/// Total box height: 2 borders + title + options + margin-top + help.
fn box_height(area_height: u16) -> u16 {
    visible_rows(area_height) as u16 + 5
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    if area.height < 5 {
        return;
    }
    f.render_widget(Clear, area);
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let border = Style::default()
        .fg(theme::popup_border())
        .bg(theme::background());
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );

    let bx = area.x;
    let by = area.y;
    let w = area.width;
    let h = area.height;

    // Title: bold $primary, one padding col in from the border.
    f.buffer_mut().set_string(
        bx + 2,
        by + 1,
        "Select Theme",
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );

    let visible = h.saturating_sub(5) as usize;
    let top = by + 2;
    let opts = options();
    let total = opts.len();
    let list_area = Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16);
    let options = Rect::new(bx + 3, top, w.saturating_sub(7), visible as u16);
    let offset = app.theme_picker.list.layout_rows(total, options);
    crate::mouse::register_region(app, list_area, crate::mouse::MouseTarget::ThemePicker);
    let current_opt = app.theme_picker.current;

    for (row, i) in (offset..total).take(visible).enumerate() {
        let y = top + row as u16;
        let is_hl = i == app.theme_picker.list.selected();
        let is_current = i == current_opt;
        // Highlight bar spans the option text (from the option's own left
        // padding up to, but not including, the scrollbar gutter).
        if is_hl {
            list_cursor::paint(f, Rect::new(bx + 3, y, w.saturating_sub(7), 1));
        }
        // The marker is styled green only; the bold comes from the row
        // highlight, so an unhighlighted current row keeps a regular `›`.
        let (base, _) = list_cursor::styles(is_hl);
        let marker_style = list_cursor::marker_style(base, is_current);
        f.buffer_mut()
            .set_string(bx + 3, y, list_cursor::marker(is_current), marker_style);
        let name_style = match is_current {
            true => base.add_modifier(Modifier::BOLD),
            false => base,
        };
        f.buffer_mut().set_string(bx + 5, y, opts[i], name_style);
    }

    // Scrollbar in the last interior column when the list overflows.
    if total > visible {
        let bar = Rect::new(bx + w - 4, top, 1, visible as u16);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::ThemePicker,
            bar,
            total as u16,
            visible as u16,
            offset as u16,
        );
    }

    hint_line::draw(f, bx + 2, by + h - 2, HINTS);
}

const HINTS: &[Hint] = &[(key::NAV, action::PREVIEW), hints::SELECT, hints::CANCEL];

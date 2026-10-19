//! `/model` picker bottom-app: a bordered box with a title, the model option
//! list (a leading Default row with a `(currently …)` hint, `›` on the current
//! choice, no live preview), and a hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{hint_line, list_cursor, scrollbar, theme};
use crate::app::App;
use crate::model_picker::{is_current, option_count};

/// Draw the whole screen with the picker replacing the input box; the chat
/// shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let height = box_height(app, area.height);
    let kind = super::bottom_app::Kind::Model;
    super::bottom_app::draw(app, f, area, height, kind, draw_box);
}

/// Visible option rows: `min(count, 50vh)`.
fn visible_rows(app: &App, area_height: u16) -> usize {
    (area_height as usize / 2).min(option_count(app))
}

/// Total box height: 2 borders + title + options + margin-top + help.
fn box_height(app: &App, area_height: u16) -> u16 {
    visible_rows(app, area_height) as u16 + 5
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

    f.buffer_mut().set_string(
        bx + 2,
        by + 1,
        "Select Model",
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );

    let visible = h.saturating_sub(5) as usize;
    let top = by + 2;
    let total = option_count(app);
    let list_area = Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16);
    // Without overflow there is no scrollbar gutter, so the options span one
    // column further right (Textual `OptionList` option width).
    let has_scrollbar = total > visible;
    let gutter = if has_scrollbar { 7 } else { 6 };
    let options = Rect::new(bx + 3, top, w.saturating_sub(gutter), visible as u16);
    let offset = app.model_picker.list.layout_rows(total, options);
    crate::mouse::register_region(app, list_area, crate::mouse::MouseTarget::ModelPicker);

    for (row, i) in (offset..total).take(visible).enumerate() {
        let y = top + row as u16;
        draw_option(
            app,
            f,
            Rect {
                y,
                height: 1,
                ..options
            },
            i,
        );
    }

    if has_scrollbar {
        let bar = Rect::new(bx + w - 4, top, 1, visible as u16);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::ModelPicker,
            bar,
            total as u16,
            visible as u16,
            offset as u16,
        );
    }

    hint_line::draw(f, bx + 2, by + h - 2, hint_line::PICK_HINTS);
}

/// One option row: the `›`/blank marker, the label, and (row 0) the dim hint.
fn draw_option(app: &App, f: &mut Frame, row: Rect, i: usize) {
    let is_hl = i == app.model_picker.list.selected();
    let current = is_current(app, i);
    let (x, y) = (row.x, row.y);
    if is_hl {
        list_cursor::paint(f, row);
    }
    // A highlighted row is bold across the whole option (block cursor); a
    // current-but-unhighlighted row bolds only its label.
    let (base, dim) = list_cursor::styles(is_hl);
    let marker_style = list_cursor::marker_style(base, current);
    f.buffer_mut()
        .set_string(x, y, list_cursor::marker(current), marker_style);
    let name_style = match current {
        true => base.add_modifier(Modifier::BOLD),
        false => base,
    };
    let label = if i == 0 {
        "Default"
    } else {
        &app.model_picker.models[i - 1].display_name
    };
    f.buffer_mut().set_string(x + 2, y, label, name_style);

    if i == 0 {
        let hint = format!("  (currently {})", app.model_picker.default_display_name);
        let hint_style = dim;
        let hint_x = x + 2 + label.chars().count() as u16;
        f.buffer_mut().set_string(hint_x, y, hint, hint_style);
    }
}

//! `/model` picker bottom-app: a bordered box with a title, the model option
//! list (a leading Default row with a `(currently …)` hint, `›` on the current
//! choice, no live preview), and a hint. Mirrors Python's `ModelPickerApp`.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{hint_line, list_cursor, list_scroll, scrollbar, theme};
use crate::app::App;
use crate::model_picker::{is_current, option_count};

/// Draw the whole screen with the picker replacing the input box, matching the
/// Textual layout where `#chat` shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let height = box_height(app, area.height);
    let kind = super::bottom_app::Kind::Model;
    super::bottom_app::draw(app, f, area, height, kind, draw_box);
}

/// Visible option rows: `min(count, 50vh)` (Textual `max-height: 50vh`).
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
    let offset = reconcile_scroll(app, total, visible);
    crate::mouse::register_region(
        app,
        Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16),
        crate::mouse::MouseTarget::ModelPicker,
    );
    // Without overflow there is no scrollbar gutter, so the highlight bar fills
    // one column further right (Textual `OptionList` option width).
    let has_scrollbar = total > visible;

    for (row, i) in (offset..total).take(visible).enumerate() {
        draw_option(app, f, bx, top + row as u16, w, i, has_scrollbar);
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
fn draw_option(app: &App, f: &mut Frame, bx: u16, y: u16, w: u16, i: usize, has_scrollbar: bool) {
    let is_hl = i == app.model_picker.selected;
    let current = is_current(app, i);
    if is_hl {
        let gutter = if has_scrollbar { 7 } else { 6 };
        list_cursor::paint(f, Rect::new(bx + 3, y, w.saturating_sub(gutter), 1));
    }
    // A highlighted row is bold across the whole option (Textual block cursor);
    // a current-but-unhighlighted row bolds only its label (Python "bold" style).
    let (base, dim) = list_cursor::styles(is_hl);
    let marker_style = list_cursor::marker_style(base, current);
    f.buffer_mut()
        .set_string(bx + 3, y, list_cursor::marker(current), marker_style);
    let name_style = match current {
        true => base.add_modifier(Modifier::BOLD),
        false => base,
    };
    let label = if i == 0 {
        "Default"
    } else {
        &app.model_picker.models[i - 1].display_name
    };
    f.buffer_mut().set_string(bx + 5, y, label, name_style);

    if i == 0 {
        let hint = format!("  (currently {})", app.model_picker.default_display_name);
        let hint_style = dim;
        let hint_x = bx + 5 + label.chars().count() as u16;
        f.buffer_mut().set_string(hint_x, y, hint, hint_style);
    }
}

/// Keep the highlighted option visible, the wheel's free scroll aside.
fn reconcile_scroll(app: &mut App, total: usize, visible: usize) -> usize {
    let state = &mut app.model_picker;
    let offset = match state.free_scroll {
        true => state.scroll.min(total.saturating_sub(visible)),
        false => {
            let selected = state.selected.min(total.saturating_sub(1));
            list_scroll::follow(state.scroll, visible, total, selected..selected + 1, |_| {
                true
            })
        }
    };
    state.scroll = offset;
    offset
}

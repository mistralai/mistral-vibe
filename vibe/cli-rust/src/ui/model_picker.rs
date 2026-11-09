//! `/model` picker bottom-app: a bordered box with a title, the `/` search row,
//! the option list (a leading Default row with a `(currently …)` hint, `›` on
//! the current choice, no live preview), and a hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{hint_line, list_cursor, scrollbar, search_field, theme};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::model_picker::{self, option_count, Row};
use crate::search_field as search_model;

/// Footer hints: the shared pick hints plus search.
const HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER, action::SAVE),
    ("s", action::SESSION_ONLY),
    hints::SEARCH,
    hints::CANCEL,
];

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

/// Total box height: 2 borders + title + search + blank + options + help.
fn box_height(app: &App, area_height: u16) -> u16 {
    visible_rows(app, area_height) as u16 + 6
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    if area.height < 6 {
        return;
    }
    f.render_widget(Clear, area);
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let border = theme::border_style().bg(theme::background());
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

    draw_search(app, f, area);

    let visible = h.saturating_sub(6) as usize;
    let top = by + 4;
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

    let search = &app.model_picker.search;
    let hints = search_model::hints(search.focused, !search.query.is_empty(), HINTS);
    hint_line::draw(f, bx + 2, by + h - 2, &hints);
}

/// The search row under the title; the field owns its mouse like Python's `Input`.
fn draw_search(app: &mut App, f: &mut Frame, area: Rect) {
    let row = Rect::new(area.x + 3, area.y + 2, area.width.saturating_sub(6), 1);
    let input = search_field::draw_row(
        f,
        row,
        &app.model_picker.search,
        "Search models",
        app.view.cursor_on,
        theme::background(),
    );
    app.model_picker.search.area = input;
    crate::mouse::register_region(app, input, crate::mouse::MouseTarget::ModelPicker);
    // A box drag never selects the field.
    let field = (input.y, input.x, input.right().saturating_sub(1));
    app.view.bottom_app_selection_chrome.push(field);
}

/// One option row: the `›`/blank marker, the label, and (Default row) the dim hint.
fn draw_option(app: &App, f: &mut Frame, row: Rect, i: usize) {
    let is_hl = i == app.model_picker.list.selected();
    let current = model_picker::is_current(app, i);
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
    match model_picker::rows(app).get(i) {
        Some(Row::Note) => {
            f.buffer_mut()
                .set_string(x + 2, y, model_picker::NO_MATCHES_LABEL, dim);
        }
        Some(Row::Default) => {
            let label = "Default";
            f.buffer_mut().set_string(x + 2, y, label, name_style);
            let hint = format!("  (currently {})", app.model_picker.default_display_name);
            let hint_x = x + 2 + label.chars().count() as u16;
            f.buffer_mut().set_string(hint_x, y, hint, dim);
        }
        Some(Row::Model(m)) => {
            let label = app.model_picker.models[*m].display_name.as_str();
            f.buffer_mut().set_string(x + 2, y, label, name_style);
        }
        None => {}
    }
}

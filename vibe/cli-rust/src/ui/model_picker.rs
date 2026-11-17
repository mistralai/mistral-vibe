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
use crate::model_picker::{self, Row};
use crate::mouse::MouseTarget;
use crate::search_field as search_model;

/// Footer hints: the shared pick hints plus search.
const HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER, action::SAVE),
    ("s", action::SESSION_ONLY),
    hints::SEARCH,
    hints::CANCEL,
];

/// Rows around the options: 2 borders, title, search, blank, margin-top, help.
const CHROME: u16 = 7;

/// Draw the whole screen with the picker replacing the input box; the chat
/// shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let rows = model_picker::rows(app);
    let visible = (area.height as usize / 2).min(rows.len());
    let height = visible as u16 + CHROME;
    let kind = super::bottom_app::Kind::Model;
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, &rows)
    });
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, rows: &[Row]) {
    if area.height < CHROME {
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

    search_field::draw_under_title(
        app,
        f,
        area,
        |app| &mut app.model_picker.search,
        "Search models",
        MouseTarget::ModelPicker,
    );

    let visible = h.saturating_sub(CHROME) as usize;
    let top = by + 4;
    let total = rows.len();
    let list_area = Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16);
    // Without overflow there is no scrollbar gutter, so the options span one
    // column further right (Textual `OptionList` option width).
    let has_scrollbar = total > visible;
    let gutter = if has_scrollbar { 7 } else { 6 };
    let options = Rect::new(bx + 3, top, w.saturating_sub(gutter), visible as u16);
    let lines: Vec<_> = rows
        .iter()
        .enumerate()
        .map(|(i, row)| row.selectable().then_some(i))
        .collect();
    let offset = app.model_picker.list.layout(&lines, options);
    crate::mouse::register_region(app, list_area, MouseTarget::ModelPicker);

    let current = model_picker::current_row(app);
    for (y, i) in (top..).zip((offset..total).take(visible)) {
        let marks = Marks {
            highlighted: i == app.model_picker.list.selected(),
            current: current == Some(rows[i]),
        };
        draw_option(
            app,
            f,
            Rect {
                y,
                height: 1,
                ..options
            },
            rows[i],
            marks,
        );
    }

    if has_scrollbar {
        let bar = Rect::new(bx + w - 4, top, 1, visible as u16);
        scrollbar::draw(
            app,
            f,
            MouseTarget::ModelPicker,
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

/// Whether a row takes the list cursor and whether it is the current choice.
struct Marks {
    highlighted: bool,
    current: bool,
}

/// One option row: the `›`/blank marker, the label, and (Default row) the dim hint.
fn draw_option(app: &App, f: &mut Frame, area: Rect, row: Row, marks: Marks) {
    let is_hl = marks.highlighted && row.selectable();
    let current = marks.current;
    let (x, y) = (area.x, area.y);
    if is_hl {
        list_cursor::paint(f, area);
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
    match row {
        Row::Note => {
            f.buffer_mut()
                .set_string(x + 2, y, model_picker::NO_MATCHES_LABEL, dim);
        }
        Row::Default => {
            let label = model_picker::DEFAULT_LABEL;
            f.buffer_mut().set_string(x + 2, y, label, name_style);
            let hint = format!("  (currently {})", app.model_picker.default_display_name);
            let hint_x = x + 2 + label.chars().count() as u16;
            f.buffer_mut().set_string(hint_x, y, hint, dim);
        }
        Row::Model(m) => {
            let label = app.model_picker.models[m].display_name.as_str();
            f.buffer_mut().set_string(x + 2, y, label, name_style);
        }
    }
}

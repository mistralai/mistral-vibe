//! `/thinking` picker bottom-app: a bordered box with a title, the thinking-level
//! option list (`›` on the current level), and a hint. Mirrors Python's
//! `ThinkingPickerApp`.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{hint_line, list_cursor, theme};
use crate::app::App;
use crate::config_write::Scope;
use crate::hints::{self, action, key, Hint};

/// Draw the whole screen with the picker replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let kind = super::bottom_app::Kind::Thinking;
    super::bottom_app::draw(app, f, area, box_height(app), kind, draw_box);
}

fn box_height(app: &App) -> u16 {
    app.thinking_picker.levels.len() as u16 + 5
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
        "Select Thinking Level",
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );
    let top = by + 2;
    let rows = app.thinking_picker.levels.len();
    for (row, i) in (0..rows).enumerate() {
        draw_option(app, f, bx, top + row as u16, w, i);
    }
    hint_line::draw(f, bx + 2, by + h - 2, hints(app));
}

/// After a session-only model pick, Enter keeps the level for the session too.
const SESSION_HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER_S, action::SESSION_ONLY),
    hints::CANCEL,
];

fn hints(app: &App) -> &'static [Hint] {
    match crate::thinking_picker::enter_scope(app) {
        Scope::Saved => hint_line::PICK_HINTS,
        Scope::Session => SESSION_HINTS,
    }
}

fn draw_option(app: &App, f: &mut Frame, bx: u16, y: u16, w: u16, i: usize) {
    let is_hl = i == app.thinking_picker.selected;
    let current = crate::thinking_picker::is_current(app, i);
    if is_hl {
        list_cursor::paint(f, Rect::new(bx + 3, y, w.saturating_sub(6), 1));
    }
    let (base, _) = list_cursor::styles(is_hl);
    let marker_style = list_cursor::marker_style(base, current);
    f.buffer_mut()
        .set_string(bx + 3, y, list_cursor::marker(current), marker_style);
    let name_style = match current {
        true => base.add_modifier(Modifier::BOLD),
        false => base,
    };
    let label = app.thinking_picker.levels[i].as_str();
    let display = capitalize(label);
    f.buffer_mut().set_string(bx + 5, y, display, name_style);
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

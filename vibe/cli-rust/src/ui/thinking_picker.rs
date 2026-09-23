//! `/thinking` picker bottom-app: a bordered box with a title, the thinking-level
//! option list (`›` on the current level), and a hint. Mirrors Python's
//! `ThinkingPickerApp`.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::model_picker::draw_help;
use super::theme_picker::marker_green;
use super::{bottom_bar, loading, theme, transcript};
use crate::app::App;
use crate::thinking_picker::THINKING_LEVELS;

/// Draw the whole screen with the picker replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let picker_height = box_height();
    let chunks = super::bottom_app_chunks(app, area, loading_height, picker_height);
    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    draw_box(app, f, chunks[2]);
    super::todo::draw_row(app, f, chunks[4]);
    bottom_bar::draw(app, f, chunks[3]);
}

fn box_height() -> u16 {
    THINKING_LEVELS.len() as u16 + 5
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
    for (row, i) in (0..THINKING_LEVELS.len()).enumerate() {
        draw_option(app, f, bx, top + row as u16, w, i);
    }
    draw_help(f, bx + 2, by + h - 2);
}

fn draw_option(app: &App, f: &mut Frame, bx: u16, y: u16, w: u16, i: usize) {
    let is_hl = i == app.thinking_picker.selected;
    let current = crate::thinking_picker::is_current(app, i);
    if is_hl {
        let bar = Rect::new(bx + 3, y, w.saturating_sub(6), 1);
        f.buffer_mut()
            .set_style(bar, Style::default().bg(theme::block_cursor_bg()));
    }
    let row_bg = if is_hl {
        theme::block_cursor_bg()
    } else {
        theme::background()
    };
    let text_fg = if is_hl {
        theme::block_cursor_fg()
    } else {
        theme::foreground()
    };
    let marker = if current { "› " } else { "  " };
    let marker_fg = if current { marker_green() } else { text_fg };
    let mut marker_style = Style::default().fg(marker_fg).bg(row_bg);
    if is_hl {
        marker_style = marker_style.add_modifier(Modifier::BOLD);
    }
    f.buffer_mut().set_string(bx + 3, y, marker, marker_style);
    let mut name_style = Style::default().fg(text_fg).bg(row_bg);
    if is_hl || current {
        name_style = name_style.add_modifier(Modifier::BOLD);
    }
    let label = THINKING_LEVELS[i];
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

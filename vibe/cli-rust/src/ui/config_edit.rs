//! Nested `/config` value editor.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear};
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::config_edit;

pub fn draw(app: &App, f: &mut Frame, area: Rect) -> Option<(Rect, Option<Rect>)> {
    let Some(edit) = &app.config_screen.edit else {
        return None;
    };
    let width = area.width.saturating_sub(8).clamp(24, 80);
    let choices = config_edit::choices(&edit.field);
    let main_width = if edit.field.layers.is_empty() {
        width - 6
    } else {
        width.saturating_sub(43)
    };
    let total_choice_rows = choices
        .iter()
        .map(|choice| {
            wrap(
                &config_edit::choice_label(&edit.field, choice),
                main_width as usize,
            )
            .len()
        })
        .sum::<usize>();
    let choice_rows = total_choice_rows.min(config_edit::MAX_VISIBLE_CHOICES);
    let height = (choice_rows as u16 + 10).max(9);
    let x = area.x + (area.width.saturating_sub(width)) / 2;
    let y = area.y + (area.height.saturating_sub(height)) / 2;
    let box_area = Rect::new(x, y, width, height);
    f.render_widget(Clear, box_area);
    let bg = theme::surface();
    f.buffer_mut().set_style(box_area, Style::default().bg(bg));
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(theme::primary()).bg(bg))
            .title(Line::from(vec![
                Span::raw("─ "),
                Span::styled(
                    edit.field.name.as_str(),
                    Style::default().add_modifier(Modifier::BOLD),
                ),
                Span::raw(" "),
            ])),
        box_area,
    );
    let body_x = x + 3;
    let mut row = y + 2;
    if !edit.field.description.is_empty() {
        f.buffer_mut().set_stringn(
            body_x,
            row,
            &edit.field.description,
            main_width as usize,
            Style::default()
                .fg(theme::muted())
                .bg(bg)
                .add_modifier(Modifier::DIM),
        );
        row += 1;
    }
    let scroll_region =
        (!choices.is_empty()).then_some(Rect::new(body_x, row, main_width, choice_rows as u16));
    if choices.is_empty() {
        f.buffer_mut().set_stringn(
            body_x,
            row,
            &edit.draft,
            main_width as usize,
            Style::default().fg(theme::foreground()).bg(bg),
        );
    } else {
        let mut choice_y = row;
        let offset = config_edit::choice_offset(edit.choice, choices.len());
        let mut rows_left = config_edit::MAX_VISIBLE_CHOICES;
        for (index, choice) in choices
            .iter()
            .skip(offset)
            .take(config_edit::MAX_VISIBLE_CHOICES)
            .enumerate()
        {
            let label = config_edit::choice_label(&edit.field, choice);
            let lines = wrap(&label, main_width as usize);
            if lines.len() > rows_left {
                break;
            }
            let line_count = lines.len();
            let selected = offset + index == edit.choice;
            let style = if selected {
                Style::default()
                    .fg(theme::block_cursor_fg())
                    .bg(theme::block_cursor_bg())
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(theme::foreground()).bg(bg)
            };
            for line in lines {
                if selected {
                    f.buffer_mut().set_style(
                        Rect::new(body_x + 1, choice_y, main_width - 2, 1),
                        Style::default().bg(theme::block_cursor_bg()),
                    );
                }
                f.buffer_mut()
                    .set_stringn(body_x + 1, choice_y, line, main_width as usize, style);
                choice_y += 1;
            }
            rows_left -= line_count;
        }
        if total_choice_rows > choice_rows {
            let scrollbar_x = body_x + main_width - 2;
            let scrollbar = Style::default()
                .fg(theme::scrollbar())
                .bg(theme::scrollbar());
            for scrollbar_y in row..row + choice_rows as u16 {
                f.buffer_mut()
                    .set_string(scrollbar_x, scrollbar_y, " ", scrollbar);
            }
            f.buffer_mut().set_string(
                scrollbar_x,
                row + choice_rows as u16 - 1,
                "▇",
                Style::default()
                    .fg(theme::scrollbar_bg())
                    .bg(theme::scrollbar()),
            );
        }
    }
    if !edit.field.layers.is_empty() {
        let side_x = x + width - 37;
        for side_y in y + 2..y + 5 {
            f.buffer_mut().set_string(
                x + width - 39,
                side_y,
                "│",
                Style::default().fg(theme::muted()).bg(bg),
            );
        }
        f.buffer_mut().set_string(
            side_x,
            y + 2,
            "WHERE IT'S SET",
            Style::default()
                .fg(theme::muted())
                .bg(bg)
                .add_modifier(Modifier::BOLD | Modifier::DIM),
        );
        for (index, (layer, value)) in edit.field.layers.iter().take(8).enumerate() {
            let prefix = if index == 0 { "▸ " } else { "  " };
            let layer = if layer == "default" {
                "defaults"
            } else {
                layer
            };
            f.buffer_mut().set_stringn(
                side_x,
                y + 4 + index as u16,
                format!("{prefix}{layer:<9} "),
                12,
                Style::default().fg(theme::muted()).bg(bg),
            );
            f.buffer_mut().set_stringn(
                side_x + 12,
                y + 4 + index as u16,
                value,
                23,
                if index == 0 {
                    Style::default()
                        .fg(theme::foreground())
                        .bg(bg)
                        .add_modifier(Modifier::BOLD)
                } else {
                    Style::default().fg(theme::muted()).bg(bg)
                },
            );
        }
    }
    f.buffer_mut().set_stringn(
        body_x,
        y + height - 7,
        "─".repeat((width - 6) as usize),
        (width - 6) as usize,
        Style::default().fg(theme::muted()).bg(bg),
    );
    draw_target_bar(app, f, edit.target, body_x, y + height - 5, bg);
    let help = if choices.is_empty() {
        if config_edit::is_multiline(&edit.field) {
            "Ctrl+S Save  Esc Cancel  Tab Change Layer"
        } else {
            "Enter Save  Esc Cancel  Tab Change Layer"
        }
    } else {
        "↑↓/jk Navigate  Enter Select  Esc Cancel  Tab Change Layer"
    };
    f.buffer_mut().set_string(
        body_x,
        y + height - 2,
        help,
        Style::default()
            .fg(theme::muted())
            .bg(bg)
            .add_modifier(Modifier::DIM),
    );
    if !choices.is_empty() {
        let key = Style::default()
            .fg(theme::primary())
            .bg(bg)
            .add_modifier(Modifier::BOLD)
            .remove_modifier(Modifier::DIM);
        for (offset, key_text) in [(0, "↑↓/jk"), (16, "Enter"), (30, "Esc"), (42, "Tab")] {
            f.buffer_mut()
                .set_string(body_x + offset, y + height - 2, key_text, key);
        }
    }
    Some((box_area, scroll_region))
}

const TARGET_BAR_PREFIX: &str = "Save to";
const TARGET_BAR_GAP: u16 = 4;
const TARGET_MARKER_WIDTH: u16 = 2;

/// Two lines: the save targets, then each target's persistence hint beneath it.
fn draw_target_bar(app: &App, f: &mut Frame, target: usize, body_x: u16, y: u16, bg: Color) {
    let base = Style::default().bg(bg).remove_modifier(Modifier::DIM);
    let strong = base.fg(theme::foreground()).add_modifier(Modifier::BOLD);
    let plain = base.fg(theme::foreground());
    let dim = Style::default()
        .fg(theme::muted())
        .bg(bg)
        .add_modifier(Modifier::DIM);
    f.buffer_mut()
        .set_string(body_x, y, TARGET_BAR_PREFIX, strong);
    let indent = TARGET_BAR_PREFIX.chars().count() as u16 + 3;
    let (mut x, mut hint_x) = (body_x + indent, body_x + indent);
    for (index, name) in app.config_screen.targets.iter().enumerate() {
        let (label, hint) = (target_label(name), target_hint(name));
        let width = label.chars().count().max(hint.chars().count());
        if index > 0 {
            x += TARGET_BAR_GAP;
            hint_x += TARGET_BAR_GAP;
        }
        let active = index == target;
        let marker = if active { "● " } else { "○ " };
        f.buffer_mut()
            .set_string(x, y, marker, if active { plain } else { dim });
        f.buffer_mut().set_string(
            x + TARGET_MARKER_WIDTH,
            y,
            format!("{label:<width$}"),
            if active { strong } else { dim },
        );
        f.buffer_mut().set_string(
            hint_x + TARGET_MARKER_WIDTH,
            y + 1,
            format!("{hint:<width$}"),
            dim.add_modifier(Modifier::ITALIC),
        );
        x += TARGET_MARKER_WIDTH + width as u16;
        hint_x += TARGET_MARKER_WIDTH + width as u16;
    }
}

/// Python `origin_label`.
fn target_label(target: &str) -> &str {
    match target {
        "default" => "defaults",
        "overrides" => "temporary",
        "environment" => "env",
        "admin" => "your administrator",
        "user-toml" => "user config",
        "project-toml" => "project config",
        _ => target,
    }
}

/// Python `target_hint`.
fn target_hint(target: &str) -> &str {
    match target {
        "overrides" => "until restart",
        "project-toml" => "saved for this project",
        _ => "saved globally",
    }
}

fn wrap(text: &str, width: usize) -> Vec<&str> {
    if text.chars().count() <= width {
        return vec![text];
    }
    let split = text[..text
        .char_indices()
        .nth(width)
        .map_or(text.len(), |(i, _)| i)]
        .rfind(' ')
        .unwrap_or(width);
    vec![text[..split].trim_end(), text[split..].trim_start()]
}

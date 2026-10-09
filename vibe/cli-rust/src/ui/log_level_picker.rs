//! `/log-level` bottom-app: title, effective-source subtitle, one row per level
//! with `session` / `config` badges, and a hint. Mirrors `LogLevelPickerApp`.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::hint_line;
use super::{list_cursor, theme};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::log_level_picker::{draft_chain, options, BADGE_CONFIG, BADGE_SESSION};
use crate::observability::level::LogLevelChain;

/// Borders + title + subtitle + margin + rows + margin + help.
fn box_height() -> u16 {
    options().len() as u16 + 7
}

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let kind = super::bottom_app::Kind::LogLevel;
    super::bottom_app::draw(app, f, area, box_height(), kind, draw_box);
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    if area.height < box_height() {
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

    let (bx, by) = (area.x, area.y);
    f.buffer_mut().set_string(
        bx + 2,
        by + 1,
        "Log Level",
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );

    let chain = draft_chain(app);
    f.buffer_mut().set_string(
        bx + 2,
        by + 2,
        subtitle(&chain),
        theme::muted_style().bg(theme::background()),
    );

    crate::mouse::register_region(
        app,
        Rect::new(
            bx + 1,
            by + 4,
            area.width.saturating_sub(2),
            options().len() as u16,
        ),
        crate::mouse::MouseTarget::LogLevelPicker,
    );

    for (row, level) in options().iter().enumerate() {
        draw_row(app, f, area, by + 4 + row as u16, level, &chain);
    }
    hint_line::draw(f, bx + 2, by + area.height - 2, HINTS);
}

fn subtitle(chain: &LogLevelChain) -> String {
    let source = match (&chain.session, &chain.env, &chain.config) {
        (Some(level), _, _) => format!("session override: {level}"),
        (None, Some(level), _) => format!("env LOG_LEVEL: {level}"),
        (None, None, Some(level)) => format!("config.toml: {level}"),
        _ => "default".to_owned(),
    };
    format!("Effective: {}  ({source})", chain.effective)
}

fn draw_row(app: &App, f: &mut Frame, area: Rect, y: u16, level: &str, chain: &LogLevelChain) {
    let x = area.x + 3;
    let highlighted = crate::log_level_picker::selected_level(app) == level;
    if highlighted {
        list_cursor::paint(f, Rect::new(x, y, area.width.saturating_sub(6), 1));
    }
    let (base, _) = list_cursor::styles(highlighted);
    let current = chain.effective == level;
    let marker_style = list_cursor::marker_style(base, current);
    f.buffer_mut()
        .set_string(x, y, list_cursor::marker(current), marker_style);
    f.buffer_mut()
        .set_string(x + 2, y, format!("{level:<10}"), base);

    // Python appends badges sequentially, so brackets widen the focused one and
    // an unhighlighted row leaves a bare gap for a badge it does not own.
    let mut cursor = x + 14;
    for badge in [BADGE_SESSION, BADGE_CONFIG] {
        let set = match badge {
            BADGE_SESSION => chain.session.as_deref() == Some(level),
            _ => chain.config.as_deref() == Some(level),
        };
        let focused = highlighted && app.log_level_picker.focused_badge == badge;
        if set || highlighted {
            let style = badge_style(base, set, focused);
            // The brackets stay unstyled: only the badge text turns green.
            let text_x = if focused {
                f.buffer_mut().set_string(cursor, y, "[", base);
                cursor + 1
            } else {
                cursor
            };
            f.buffer_mut().set_string(text_x, y, badge, style);
            if focused {
                f.buffer_mut()
                    .set_string(text_x + badge.chars().count() as u16, y, "]", base);
            }
        }
        cursor += badge.chars().count() as u16 + if focused { 2 } else { 0 } + 2;
    }
}

/// Green when the badge holds this level; dim when it is neither set nor the
/// one Enter would toggle (Python `_append_badge`).
fn badge_style(base: Style, set: bool, focused: bool) -> Style {
    if set {
        return base.fg(list_cursor::current_color());
    }
    if focused {
        base
    } else {
        base.add_modifier(Modifier::DIM)
    }
}

const HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::LEFT_RIGHT, action::SWITCH_BADGE),
    (key::ENTER, action::TOGGLE),
    hints::CLOSE,
];

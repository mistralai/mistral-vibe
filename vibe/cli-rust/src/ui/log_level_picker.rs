//! `/log-level` bottom-app: title, effective-source subtitle, one row per level
//! with `session` / `config` badges, and a hint. Mirrors `LogLevelPickerApp`.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::theme_picker::marker_green;
use super::{bottom_bar, loading, theme, transcript};
use crate::app::App;
use crate::log_level_picker::{draft_chain, options, BADGE_CONFIG, BADGE_SESSION};
use crate::observability::level::LogLevelChain;

/// Borders + title + subtitle + margin + rows + margin + help.
fn box_height() -> u16 {
    options().len() as u16 + 7
}

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let chunks = super::bottom_app_chunks(app, area, loading_height, box_height());

    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Blocked);
    draw_box(app, f, chunks[2]);
    super::todo::draw_row(app, f, chunks[4]);
    bottom_bar::draw(app, f, chunks[3]);
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
    draw_help(f, bx + 2, by + area.height - 2);
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
    let (row_bg, text_fg) = if highlighted {
        (theme::block_cursor_bg(), theme::block_cursor_fg())
    } else {
        (theme::background(), theme::foreground())
    };
    if highlighted {
        let bar = Rect::new(x, y, area.width.saturating_sub(6), 1);
        f.buffer_mut().set_style(bar, Style::default().bg(row_bg));
    }

    let mut base = Style::default().fg(text_fg).bg(row_bg);
    if highlighted {
        base = base.add_modifier(Modifier::BOLD);
    }
    let marker = if chain.effective == level {
        "› "
    } else {
        "  "
    };
    let marker_style = if chain.effective == level {
        base.fg(marker_green())
    } else {
        base
    };
    f.buffer_mut().set_string(x, y, marker, marker_style);
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
        return base.fg(marker_green());
    }
    if focused {
        base
    } else {
        base.add_modifier(Modifier::DIM)
    }
}

fn draw_help(f: &mut Frame, x: u16, y: u16) {
    let key = Style::default()
        .fg(theme::primary())
        .bg(theme::background())
        .add_modifier(Modifier::BOLD);
    let label = theme::muted_style().bg(theme::background());
    let mut cx = x;
    for (k, l) in [
        ("↑↓/jk", " Navigate  "),
        ("←/→", " Switch badge  "),
        ("Enter", " Toggle  "),
        ("Esc", " Close"),
    ] {
        f.buffer_mut().set_string(cx, y, k, key);
        cx += k.chars().count() as u16;
        f.buffer_mut().set_string(cx, y, l, label);
        cx += l.chars().count() as u16;
    }
}

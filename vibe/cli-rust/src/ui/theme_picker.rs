//! `/theme` picker bottom-app: a bordered box with a title, the theme option
//! list (live-preview highlight, `›` on the current theme), and a hint. Mirrors
//! Python's `ThemePickerApp` and its TCSS in `app.tcss`.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{bottom_bar, loading, scrollbar, theme, transcript};
use crate::app::App;
use crate::theme_picker::options;

/// The `›` marker's `green`. Under an ANSI theme it's a raw terminal green; on
/// truecolour themes it's mapped through Textual's terminal ANSI theme (Monokai
/// for dark, Alabaster for light).
pub(crate) fn marker_green() -> Color {
    let active = theme::active();
    if active.name.starts_with("ansi-") {
        Color::Green
    } else if active.dark {
        Color::Rgb(0x98, 0xE0, 0x24)
    } else {
        Color::Rgb(0x44, 0x8C, 0x27)
    }
}

/// Draw the whole screen with the picker replacing the input box, matching the
/// Textual layout where `#chat` shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));

    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let picker_height = box_height(area.height);
    let chunks = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(loading_height),
        Constraint::Length(picker_height),
        Constraint::Length(1),
    ])
    .split(area);

    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Blocked);
    draw_box(app, f, chunks[2]);
    bottom_bar::draw(app, f, chunks[3]);
}

/// Visible option rows: `min(count, 50vh)` (Textual `max-height: 50vh`).
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
    let offset = reconcile_scroll(app, total, visible);
    crate::mouse::register_region(
        app,
        Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16),
        crate::mouse::MouseTarget::ThemePicker,
    );
    let current_opt = app.theme_picker.current;

    for (row, i) in (offset..total).take(visible).enumerate() {
        let y = top + row as u16;
        let is_hl = i == app.theme_picker.selected;
        let is_current = i == current_opt;
        // Highlight bar spans the option text (from the option's own left
        // padding up to, but not including, the scrollbar gutter).
        if is_hl {
            let bar = Rect::new(bx + 3, y, w.saturating_sub(7), 1);
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
        let marker = if is_current { "› " } else { "  " };
        let marker_fg = if is_current { marker_green() } else { text_fg };
        // Python styles the marker green only; the bold comes from the row
        // highlight, so an unhighlighted current row keeps a regular `›`.
        let mut marker_style = Style::default().fg(marker_fg).bg(row_bg);
        if is_hl {
            marker_style = marker_style.add_modifier(Modifier::BOLD);
        }
        f.buffer_mut().set_string(bx + 3, y, marker, marker_style);
        let mut name_style = Style::default().fg(text_fg).bg(row_bg);
        if is_hl || is_current {
            name_style = name_style.add_modifier(Modifier::BOLD);
        }
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

    draw_help(f, bx + 2, by + h - 2);
}

/// Keep the highlighted option visible (Textual `scroll_to_highlight`).
fn reconcile_scroll(app: &mut App, total: usize, visible: usize) -> usize {
    let sel = app.theme_picker.selected.min(total.saturating_sub(1));
    let mut off = app.theme_picker.scroll;
    if app.theme_picker.free_scroll {
        off = off.min(total.saturating_sub(visible));
    } else if sel < off {
        off = sel;
    } else if sel >= off + visible {
        off = sel + 1 - visible;
    }
    off = off.min(total.saturating_sub(visible));
    app.theme_picker.scroll = off;
    off
}

/// The hint line: `↑↓/jk` `Enter` `Esc` keys in bold $primary, labels in $text-muted.
fn draw_help(f: &mut Frame, x: u16, y: u16) {
    let key = Style::default()
        .fg(theme::primary())
        .bg(theme::background())
        .add_modifier(Modifier::BOLD);
    let label = theme::muted_style().bg(theme::background());
    let mut cx = x;
    for (k, l) in [
        ("↑↓/jk", " Preview  "),
        ("Enter", " Select  "),
        ("Esc", " Cancel"),
    ] {
        f.buffer_mut().set_string(cx, y, k, key);
        cx += k.chars().count() as u16;
        f.buffer_mut().set_string(cx, y, l, label);
        cx += l.chars().count() as u16;
    }
}

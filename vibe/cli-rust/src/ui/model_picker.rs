//! `/model` picker bottom-app: a bordered box with a title, the model option
//! list (a leading Default row with a `(currently …)` hint, `›` on the current
//! choice, no live preview), and a hint. Mirrors Python's `ModelPickerApp`.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::theme_picker::marker_green;
use super::{bottom_bar, loading, scrollbar, theme, transcript};
use crate::app::App;
use crate::model_picker::{is_current, option_count};

/// Draw the whole screen with the picker replacing the input box, matching the
/// Textual layout where `#chat` shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));

    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let picker_height = box_height(app, area.height);
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

    draw_help(f, bx + 2, by + h - 2);
}

/// One option row: the `›`/blank marker, the label, and (row 0) the dim hint.
fn draw_option(app: &App, f: &mut Frame, bx: u16, y: u16, w: u16, i: usize, has_scrollbar: bool) {
    let is_hl = i == app.model_picker.selected;
    let current = is_current(app, i);
    if is_hl {
        let gutter = if has_scrollbar { 7 } else { 6 };
        let bar = Rect::new(bx + 3, y, w.saturating_sub(gutter), 1);
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

    // A highlighted row is bold across the whole option (Textual block cursor);
    // a current-but-unhighlighted row bolds only its label (Python "bold" style).
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
    let label = if i == 0 {
        "Default"
    } else {
        &app.model_picker.models[i - 1].display_name
    };
    f.buffer_mut().set_string(bx + 5, y, label, name_style);

    if i == 0 {
        let hint = format!("  (currently {})", app.model_picker.default_display_name);
        let hint_fg = if is_hl { text_fg } else { theme::muted() };
        let mut hint_style = theme::dim(hint_fg).bg(row_bg);
        if is_hl {
            hint_style = hint_style.add_modifier(Modifier::BOLD);
        }
        let hint_x = bx + 5 + label.chars().count() as u16;
        f.buffer_mut().set_string(hint_x, y, hint, hint_style);
    }
}

/// Keep the highlighted option visible (Textual `scroll_to_highlight`).
fn reconcile_scroll(app: &mut App, total: usize, visible: usize) -> usize {
    let sel = app.model_picker.selected.min(total.saturating_sub(1));
    let mut off = app.model_picker.scroll;
    if app.model_picker.free_scroll {
        off = off.min(total.saturating_sub(visible));
    } else if sel < off {
        off = sel;
    } else if sel >= off + visible {
        off = sel + 1 - visible;
    }
    off = off.min(total.saturating_sub(visible));
    app.model_picker.scroll = off;
    off
}

/// The hint line: `↑↓/jk` `Enter` `Esc` keys in bold $primary, labels in $text-muted.
pub(crate) fn draw_help(f: &mut Frame, x: u16, y: u16) {
    let key = Style::default()
        .fg(theme::primary())
        .bg(theme::background())
        .add_modifier(Modifier::BOLD);
    let label = theme::muted_style().bg(theme::background());
    let mut cx = x;
    for (k, l) in [
        ("↑↓/jk", " Navigate  "),
        ("Enter", " Select  "),
        ("Esc", " Cancel"),
    ] {
        f.buffer_mut().set_string(cx, y, k, key);
        cx += k.chars().count() as u16;
        f.buffer_mut().set_string(cx, y, l, label);
        cx += l.chars().count() as u16;
    }
}

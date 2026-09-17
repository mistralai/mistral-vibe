//! `/config` settings screen: a centered modal listing config rows in Popular
//! and Advanced sections. Mirrors Python's `ConfigScreen` and the layout
//! constants in `screens/config/_common.py`.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear};
use ratatui::Frame;

use super::{config_edit, config_options, scrollbar, theme};
use crate::app::App;
use crate::config::{self, ConfigField};

pub(super) const NAME_W: usize = 38;
pub(super) const VALUE_W: usize = 32;
pub(super) const GAP: usize = 2;
pub(super) const CURSOR_W: usize = 2;
const ROW_W: usize = CURSOR_W + NAME_W + GAP + VALUE_W;
const OPT_W: u16 = 76;
pub(super) const POPULAR: &str = "Popular settings";
pub(super) const ADVANCED: &str = "Advanced settings";
const HELP: &[(&str, &str)] = &[
    ("type", "Filter"),
    ("↑↓", "Navigate"),
    ("Enter", "Edit"),
    ("Ctrl+R", "Reset"),
    ("Esc", "Close"),
];

/// One content line of the option list.
pub(super) enum Opt {
    /// A section rule with a brighter, bold centered label.
    Section(String),
    /// The dim `SETTING ... VALUE` column header.
    Header(String),
    Blank,
    Row {
        selected: bool,
        name: String,
        value: String,
    },
}

/// Draw the modal over `area` (the full screen); the base UI shows through the
/// margins, like Textual's centered `ModalScreen`.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    app.config_screen.area = area;
    let w = ((area.width as u32 * 96 / 100) as u16).min(92);
    let h = (area.height as u32 * 96 / 100) as u16;
    if w < 20 || h < 8 {
        return;
    }
    let bx = area.x + (area.width - w) / 2;
    let by = area.y + (area.height - h) / 2;
    let box_area = Rect::new(bx, by, w, h);
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::Blocked);

    // Clear resets the cells so the base UI does not bleed through the interior;
    // `set_style` alone keeps the old glyphs.
    f.render_widget(Clear, box_area);
    let surface = Style::default().bg(theme::surface());
    f.buffer_mut().set_style(box_area, surface);
    let primary = Style::default().fg(theme::primary()).bg(theme::surface());
    let title = Line::from(vec![
        Span::styled("─", primary),
        Span::styled(" Settings ", primary.add_modifier(Modifier::BOLD)),
    ]);
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(primary)
        .title(title);
    f.render_widget(block, box_area);

    let cx = bx + 3; // border + padding-left 2
    let cy = by + 2; // border + padding-top 1

    // Search line: $primary label, dim-primary placeholder.
    f.buffer_mut()
        .set_string(cx, cy, "Filter: ", primary.add_modifier(Modifier::BOLD));
    f.buffer_mut().set_string(
        cx + 8,
        cy,
        if app.config_screen.query.is_empty() {
            "type to filter"
        } else {
            &app.config_screen.query
        },
        Style::default()
            .fg(theme::primary())
            .bg(theme::surface())
            .add_modifier(Modifier::DIM),
    );

    draw_options(app, f, bx, cy + 2, w, by + h - 2);
    draw_help(f, cx, by + h - 2);
    if let Some((surface, region)) = config_edit::draw(app, f, area) {
        crate::mouse::register_region(app, surface, crate::mouse::MouseTarget::Blocked);
        if let Some(region) = region {
            crate::mouse::register_region(app, region, crate::mouse::MouseTarget::ConfigEditor);
        }
    }
}

/// The option list: a `$background` panel of section headers and field rows.
fn draw_options(app: &mut App, f: &mut Frame, bx: u16, top: u16, w: u16, help_y: u16) {
    let opt_x = bx + 3 + (w - 6 - OPT_W) / 2;
    let visible = help_y.saturating_sub(top + 1);
    let region = Rect::new(opt_x, top, OPT_W, visible);
    crate::mouse::register_region(app, region, crate::mouse::MouseTarget::Config);
    f.buffer_mut()
        .set_style(region, Style::default().bg(theme::background()));
    let bg = theme::background();
    let fields: Vec<ConfigField> = config::filtered(app).into_iter().cloned().collect();
    let lines = config_options::build_lines(
        &fields,
        app.config_screen.selected,
        app.config_screen.query.trim().is_empty() || fields.len() > 5,
    );
    let total = lines.len() as u16;
    let offset = reconcile_scroll(app, &lines, visible);
    for (row, line) in lines.iter().skip(offset).take(visible as usize).enumerate() {
        let y = top + row as u16;
        match line {
            Opt::Blank => {}
            Opt::Section(label) => draw_section(f, opt_x, y, label, bg),
            Opt::Header(text) => {
                f.buffer_mut().set_string(
                    opt_x,
                    y,
                    text,
                    Style::default()
                        .fg(theme::muted())
                        .bg(bg)
                        .add_modifier(Modifier::DIM),
                );
            }
            Opt::Row {
                selected,
                name,
                value,
            } => {
                let cursor = if *selected { "▸ " } else { "  " };
                let buf = f.buffer_mut();
                buf.set_string(
                    opt_x,
                    y,
                    cursor,
                    Style::default()
                        .fg(theme::primary())
                        .bg(bg)
                        .add_modifier(Modifier::BOLD),
                );
                buf.set_stringn(
                    opt_x + CURSOR_W as u16,
                    y,
                    name,
                    NAME_W,
                    Style::default()
                        .fg(theme::foreground())
                        .bg(bg)
                        .add_modifier(Modifier::BOLD),
                );
                buf.set_stringn(
                    opt_x + (CURSOR_W + NAME_W + GAP) as u16,
                    y,
                    value,
                    VALUE_W,
                    (if *selected {
                        Style::default().add_modifier(Modifier::BOLD)
                    } else {
                        Style::default()
                    })
                    .fg(theme::muted())
                    .bg(bg)
                    .remove_modifier(Modifier::DIM),
                );
            }
        }
    }

    // Scrollbar in the stable gutter (last column) when the list overflows.
    if total > visible {
        let bar = Rect::new(opt_x + OPT_W - 1, top, 1, visible);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::Config,
            bar,
            total,
            visible,
            offset as u16,
        );
    }
}

/// Keep the highlighted field visible by scrolling the minimum amount, mirroring
/// Textual's `scroll_to_highlight`. Returns the new top line offset.
fn reconcile_scroll(app: &mut App, lines: &[Opt], visible: u16) -> usize {
    let visible = visible as usize;
    let total = lines.len();
    let sel_line = lines
        .iter()
        .position(|l| matches!(l, Opt::Row { selected: true, .. }))
        .unwrap_or(0);
    let mut off = app.config_screen.scroll;
    if !app.config_screen.free_scroll {
        if sel_line < off {
            off = sel_line;
        } else if sel_line >= off + visible {
            off = sel_line + 1 - visible;
        }
    }
    off = off.min(total.saturating_sub(visible));
    app.config_screen.scroll = off;
    off
}

/// A centered section rule with the dim dashes and a brighter bold label.
fn draw_section(f: &mut Frame, opt_x: u16, y: u16, label: &str, bg: Color) {
    let pad = ROW_W.saturating_sub(label.chars().count() + 2);
    let left = pad / 2;
    let dashes_l = "─".repeat(left);
    let mid = format!(" {label} ");
    let dashes_r = "─".repeat(pad - left);
    let buf = f.buffer_mut();
    buf.set_string(
        opt_x,
        y,
        &dashes_l,
        Style::default()
            .fg(theme::muted())
            .bg(bg)
            .add_modifier(Modifier::DIM),
    );
    let mx = opt_x + dashes_l.chars().count() as u16;
    buf.set_string(
        mx,
        y,
        &mid,
        Style::default()
            .fg(theme::foreground())
            .bg(bg)
            .add_modifier(Modifier::BOLD),
    );
    let rx = mx + mid.chars().count() as u16;
    buf.set_string(
        rx,
        y,
        &dashes_r,
        Style::default()
            .fg(theme::muted())
            .bg(bg)
            .add_modifier(Modifier::DIM),
    );
}

/// The bottom shortcut hint: `$primary` keys, muted labels, on the surface.
fn draw_help(f: &mut Frame, cx: u16, y: u16) {
    let mut x = cx;
    for (i, (key, label)) in HELP.iter().enumerate() {
        if i > 0 {
            x += 2;
        }
        f.buffer_mut().set_string(
            x,
            y,
            key,
            Style::default()
                .fg(theme::primary())
                .bg(theme::surface())
                .add_modifier(Modifier::BOLD),
        );
        x += key.chars().count() as u16 + 1;
        f.buffer_mut().set_string(
            x,
            y,
            label,
            Style::default()
                .fg(theme::muted())
                .bg(theme::surface())
                .add_modifier(Modifier::DIM),
        );
        x += label.chars().count() as u16;
    }
}

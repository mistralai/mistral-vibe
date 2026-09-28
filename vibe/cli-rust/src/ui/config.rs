//! `/config` settings screen: a centered modal listing config rows in Popular
//! and Advanced sections. Mirrors Python's `ConfigScreen` and the layout
//! constants in `screens/config/_common.py`.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear, Paragraph};
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
        config_edit::draw_too_small(
            app,
            f,
            area,
            "Enlarge terminal to browse settings. Esc Close",
        );
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
        if app.config_screen.query.is_empty() {
            primary.add_modifier(Modifier::DIM)
        } else {
            primary
        },
    );

    draw_options(app, f, bx, cy + 2, w, by + h - 2);
    draw_help(f, Rect::new(cx, by + h - 2, w - 6, 1));
    config_edit::draw(app, f, area);
}

/// The option list: a `$background` panel of section headers and field rows.
fn draw_options(app: &mut App, f: &mut Frame, bx: u16, top: u16, w: u16, help_y: u16) {
    let opt_width = OPT_W.min(w.saturating_sub(6));
    let opt_x = bx + 3 + w.saturating_sub(6 + OPT_W) / 2;
    let visible = help_y.saturating_sub(top + 1);
    let region = Rect::new(opt_x, top, opt_width, visible);
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
            Opt::Section(label) => draw_section(f, Rect::new(opt_x, y, opt_width, 1), label, bg),
            Opt::Header(text) => {
                f.buffer_mut().set_stringn(
                    opt_x,
                    y,
                    text,
                    opt_width as usize,
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
                    NAME_W.min(opt_width.saturating_sub(CURSOR_W as u16) as usize),
                    Style::default()
                        .fg(theme::foreground())
                        .bg(bg)
                        .add_modifier(Modifier::BOLD),
                );
                buf.set_stringn(
                    opt_x + (CURSOR_W + NAME_W + GAP) as u16,
                    y,
                    value,
                    VALUE_W
                        .min(opt_width.saturating_sub((CURSOR_W + NAME_W + GAP) as u16) as usize),
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
        let bar = Rect::new(opt_x + opt_width - 1, top, 1, visible);
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
fn draw_section(f: &mut Frame, area: Rect, label: &str, bg: Color) {
    let pad = ROW_W.saturating_sub(label.chars().count() + 2);
    let left = pad / 2;
    let dim = Style::default()
        .fg(theme::muted())
        .bg(bg)
        .add_modifier(Modifier::DIM);
    let line = Line::from(vec![
        Span::styled("─".repeat(left), dim),
        Span::styled(
            format!(" {label} "),
            Style::default()
                .fg(theme::foreground())
                .bg(bg)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled("─".repeat(pad - left), dim),
    ]);
    f.render_widget(Paragraph::new(line), area);
}

fn draw_help(f: &mut Frame, area: Rect) {
    let mut spans = Vec::new();
    for (index, (key, label)) in HELP.iter().enumerate() {
        if index > 0 {
            spans.push(Span::raw("  "));
        }
        spans.push(Span::styled(
            *key,
            Style::default()
                .fg(theme::primary())
                .add_modifier(Modifier::BOLD),
        ));
        spans.push(Span::styled(
            format!(" {label}"),
            theme::dim(theme::muted()),
        ));
    }
    f.render_widget(
        Paragraph::new(Line::from(spans)).style(Style::default().bg(theme::surface())),
        area,
    );
}

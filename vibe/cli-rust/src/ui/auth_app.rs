//! Shared box of the MCP OAuth and connector auth bottom-apps.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{bottom_bar, loading, theme, transcript};
use crate::app::App;
use crate::utils::text::wrap_hard;

/// Columns the option list loses to the border, the padding and its own gutter.
const GUTTER: u16 = 6;

/// A run of text; `key` marks a `shortcut()` span (bold `$primary`).
pub type Span = (String, bool);

/// One option row; only selectable rows can take the highlight.
pub struct Row {
    pub spans: Vec<Span>,
    pub selectable: bool,
}

impl Row {
    pub fn note(text: &str) -> Self {
        Self {
            spans: vec![(text.to_owned(), false)],
            selectable: false,
        }
    }

    pub fn blank() -> Self {
        Self {
            spans: Vec::new(),
            selectable: false,
        }
    }

    pub fn action(spans: Vec<Span>) -> Self {
        Self {
            spans,
            selectable: true,
        }
    }
}

/// Everything one auth bottom-app paints: title, options, detail, help.
pub struct View {
    pub title: String,
    pub rows: Vec<Row>,
    /// Index of the highlighted row among the selectable ones.
    pub selected: usize,
    pub detail: Vec<Span>,
    pub help: Vec<Span>,
}

/// Draw the whole screen with the auth app replacing the input box; returns the
/// option-list viewport so the caller can route mouse clicks to it.
pub fn draw(
    app: &mut App,
    f: &mut Frame,
    area: Rect,
    view: &View,
    target: crate::mouse::MouseTarget,
) -> Rect {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));

    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let detail = detail_lines(&view.detail, area.width);
    let chunks = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(loading_height),
        Constraint::Length(box_height(view.rows.len(), detail.len())),
        Constraint::Length(1),
    ])
    .split(area);

    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Blocked);
    let list_area = draw_box(f, chunks[2], view, &detail);
    crate::mouse::register_region(app, list_area, target);
    bottom_bar::draw(app, f, chunks[3]);
    list_area
}

/// Total box height: 2 borders + title + blank + options + the detail static
/// with its `margin: 1 0` + help.
fn box_height(options: usize, detail: usize) -> u16 {
    (options + detail) as u16 + 7
}

/// Fold the detail static, whose spans are one logical line each.
fn detail_lines(detail: &[Span], width: u16) -> Vec<Vec<Span>> {
    let inner = width.saturating_sub(4) as usize;
    let mut lines: Vec<Vec<Span>> = vec![Vec::new()];
    for (text, key) in detail {
        for (index, part) in text.split('\n').enumerate() {
            if index > 0 {
                lines.push(Vec::new());
            }
            if let Some(line) = lines.last_mut() {
                line.push((part.to_owned(), *key));
            }
        }
    }
    lines
        .into_iter()
        .flat_map(|line| fold(line, inner))
        .collect()
}

/// Wrap one logical line, keeping the span a fragment came from.
fn fold(line: Vec<Span>, width: usize) -> Vec<Vec<Span>> {
    let text: String = line.iter().map(|(text, _)| text.as_str()).collect();
    if line.len() > 1 || text.chars().count() <= width {
        return vec![line];
    }
    let key = line.first().is_some_and(|(_, key)| *key);
    wrap_hard(&text, width)
        .into_iter()
        .map(|part| vec![(part, key)])
        .collect()
}

fn draw_box(f: &mut Frame, area: Rect, view: &View, detail: &[Vec<Span>]) -> Rect {
    if area.height < 5 {
        return Rect::default();
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

    f.buffer_mut().set_string(
        area.x + 2,
        area.y + 1,
        &view.title,
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );

    let top = area.y + 3;
    let highlighted = view
        .rows
        .iter()
        .enumerate()
        .filter(|(_, row)| row.selectable)
        .nth(view.selected)
        .map(|(index, _)| index);
    for (index, row) in view.rows.iter().enumerate() {
        draw_row(f, area, top + index as u16, row, highlighted == Some(index));
    }

    // `margin: 1 0` on the detail static leaves a blank row on each side.
    let mut y = top + view.rows.len() as u16 + 1;
    for line in detail {
        draw_spans(f, area.x + 2, y, line, base_style());
        y += 1;
    }
    draw_spans(
        f,
        area.x + 2,
        y + 1,
        &view.help,
        theme::muted_style().bg(theme::background()),
    );
    Rect::new(
        area.x + 1,
        top,
        area.width.saturating_sub(2),
        view.rows.len() as u16,
    )
}

fn base_style() -> Style {
    Style::default()
        .fg(theme::foreground())
        .bg(theme::background())
}

fn draw_row(f: &mut Frame, area: Rect, y: u16, row: &Row, highlighted: bool) {
    let mut style = base_style();
    if highlighted {
        let bar = Rect::new(area.x + 3, y, area.width.saturating_sub(GUTTER), 1);
        f.buffer_mut()
            .set_style(bar, Style::default().bg(theme::block_cursor_bg()));
        style = Style::default()
            .fg(theme::block_cursor_fg())
            .bg(theme::block_cursor_bg())
            .add_modifier(Modifier::BOLD);
    }
    draw_spans(f, area.x + 3, y, &row.spans, style);
}

/// Paint one line, giving every `shortcut()` span the bold `$primary` style.
fn draw_spans(f: &mut Frame, x: u16, y: u16, spans: &[Span], style: Style) {
    let mut cursor = x;
    for (text, key) in spans {
        // `shortcut()` is `b not dim $primary`, so a key drops the muted dim.
        let style = if *key {
            style
                .fg(theme::primary())
                .add_modifier(Modifier::BOLD)
                .remove_modifier(Modifier::DIM)
        } else {
            style
        };
        f.buffer_mut().set_string(cursor, y, text, style);
        cursor += text.chars().count() as u16;
    }
}

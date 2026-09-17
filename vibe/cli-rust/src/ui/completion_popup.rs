//! Shared slash-command and file-path completion popup.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders};
use ratatui::Frame;

use super::{scrollbar, theme};
use crate::app::App;
use crate::completion_manager::CompletionEntry;
use crate::utils::text::wrap_hard;

/// Popup viewport height in content rows (Python `max_height` 12 minus borders).
const MAX_VISIBLE: usize = 10;

/// One rendered terminal row: label fragment, selected flag, description fragment.
struct VisualLine {
    label: Option<String>,
    selected: bool,
    desc: String,
}

/// Reconcile the viewport offset: reveal the selection on a keyboard move, then clamp.
pub fn reconcile_scroll(app: &mut App, width: u16) {
    if app.completion.entries.is_empty() {
        app.completion.scroll = 0;
        app.completion.reveal = false;
        return;
    }
    let heights = entry_heights(app, width);
    if heights.is_empty() {
        app.completion.scroll = 0;
        return;
    }
    if app.completion.selected >= heights.len() {
        app.completion.selected = 0;
    }
    let total: usize = heights.iter().sum();
    let max_off = total.saturating_sub(MAX_VISIBLE);
    if app.completion.reveal {
        let start: usize = heights[..app.completion.selected].iter().sum();
        let end = start + heights[app.completion.selected];
        if start < app.completion.scroll {
            app.completion.scroll = start;
        } else if end > app.completion.scroll + MAX_VISIBLE {
            app.completion.scroll = end - MAX_VISIBLE;
        }
        app.completion.reveal = false;
    }
    app.completion.scroll = app.completion.scroll.min(max_off);
}

/// The popup's height in rows (border + up to `MAX_VISIBLE` content rows), else 0.
pub fn popup_height(app: &App, width: u16) -> u16 {
    let lines = build_lines(app, width);
    if lines.is_empty() {
        return 0;
    }
    (lines.len().min(MAX_VISIBLE) + 2) as u16
}

/// Width of the command column: the widest label, capped at Python's `max-width: 30%`.
fn command_width(width: u16, entries: &[CompletionEntry]) -> usize {
    let cap = if entries.iter().any(|entry| !entry.description.is_empty()) {
        (width as usize)
            .saturating_mul(30)
            .saturating_div(100)
            .saturating_sub(3)
    } else {
        (width as usize).saturating_sub(4)
    };
    let widest = entries
        .iter()
        .map(|entry| display_label(&entry.label).chars().count())
        .max()
        .unwrap_or(0);
    widest.min(cap).max(1)
}

/// Per-entry rendered height: the taller of the wrapped label and description.
fn entry_heights(app: &App, width: u16) -> Vec<usize> {
    let entries = &app.completion.entries;
    if entries.is_empty() {
        return Vec::new();
    }
    let cmd_w = command_width(width, entries);
    let desc_w = (width as usize).saturating_sub(7 + cmd_w);
    entries
        .iter()
        .map(|entry| {
            wrap_hard(&entry.label, cmd_w)
                .len()
                .max(wrap_hard(&entry.description, desc_w).len())
        })
        .collect()
}

/// Flatten entries into rendered terminal rows, wrapping label and description.
fn build_lines(app: &App, width: u16) -> Vec<VisualLine> {
    let entries = &app.completion.entries;
    if entries.is_empty() {
        return Vec::new();
    }
    let cmd_w = command_width(width, entries);
    let desc_w = (width as usize).saturating_sub(7 + cmd_w);
    let mut out: Vec<VisualLine> = Vec::new();
    for (idx, entry) in entries.iter().enumerate() {
        let label_lines = wrap_hard(display_label(&entry.label), cmd_w);
        let desc_lines = wrap_hard(&entry.description, desc_w);
        for line_idx in 0..label_lines.len().max(desc_lines.len()) {
            out.push(VisualLine {
                label: label_lines.get(line_idx).cloned(),
                selected: idx == app.completion.selected,
                desc: desc_lines.get(line_idx).cloned().unwrap_or_default(),
            });
        }
    }
    out
}

fn display_label(label: &str) -> &str {
    label.strip_prefix('@').unwrap_or(label)
}

/// Draw the popup into `area` (its full bordered box).
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    if area.width < 8 || area.height < 3 {
        return;
    }
    let entries = &app.completion.entries;
    let cmd_w = command_width(area.width, entries);
    let lines = build_lines(app, area.width);
    let total = lines.len();

    let block = Block::default().borders(Borders::ALL).border_style(
        Style::default()
            .fg(theme::popup_border())
            .bg(theme::background()),
    );
    let inner = block.inner(area);
    f.render_widget(block, area);

    let text_x = inner.x + 1; // left padding (0, 1)
    let desc_x = text_x + cmd_w as u16 + 2; // command column + margin-right 2
    let desc_w = inner.width.saturating_sub(1 + cmd_w as u16 + 2 + 1 + 1) as usize;
    let buf = f.buffer_mut();

    let offset = app
        .completion
        .scroll
        .min(total.saturating_sub(inner.height as usize));
    for (row, line) in lines
        .iter()
        .skip(offset)
        .take(inner.height as usize)
        .enumerate()
    {
        let y = inner.y + row as u16;
        if let Some(label) = &line.label {
            // Textual's `.completion-selected .completion-command` is `bold reverse`;
            // a manual color swap would be a no-op under terminal-default ansi colors.
            let mut style = Style::default()
                .fg(theme::foreground())
                .bg(theme::background())
                .add_modifier(Modifier::BOLD);
            if line.selected {
                style = style.add_modifier(Modifier::REVERSED);
            }
            buf.set_stringn(text_x, y, label, cmd_w, style);
        }
        let desc_style = if line.selected {
            Style::default()
                .fg(theme::foreground())
                .bg(theme::background())
                .add_modifier(Modifier::ITALIC)
        } else {
            theme::muted_style().bg(theme::background())
        };
        buf.set_stringn(desc_x, y, &line.desc, desc_w, desc_style);
    }

    // Scrollbar in the last content column when the list overflows the viewport.
    if total > inner.height as usize {
        let bar = Rect {
            x: desc_x + desc_w as u16,
            y: inner.y,
            width: 1,
            height: inner.height,
        };
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::Completion,
            bar,
            total as u16,
            inner.height,
            offset as u16,
        );
    }
}

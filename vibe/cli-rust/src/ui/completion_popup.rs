//! Shared slash-command and file-path completion popup.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders};
use ratatui::Frame;

use super::{scrollbar, theme};
use crate::app::App;
use crate::completion_manager::CompletionEntry;
use crate::utils::text::wrap_hard;

/// Popup viewport height in content rows (a 12-row popup minus borders).
const MAX_VISIBLE: usize = 10;
/// Between a skill's origin and its description.
const ORIGIN_SEP: &str = " · ";

/// One rendered terminal row: label fragment, entry flags, description fragment.
struct VisualLine {
    label: Option<String>,
    selected: bool,
    desc: String,
    /// Leading characters of `desc` that spell the skill origin (first row only).
    origin_chars: usize,
}

/// Column widths: command and description (origin included).
struct Columns {
    cmd: usize,
    desc: usize,
}

impl Columns {
    fn new(width: u16, entries: &[CompletionEntry]) -> Self {
        let cmd = command_width(width, entries);
        Self {
            cmd,
            desc: (width as usize).saturating_sub(7 + cmd),
        }
    }
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

/// Width of the command column: the widest label, capped at 30% of the width.
fn command_width(width: u16, entries: &[CompletionEntry]) -> usize {
    let cap = if entries
        .iter()
        .any(|entry| !entry.description.is_empty() || entry.origin.is_some())
    {
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
    let cols = Columns::new(width, entries);
    entries
        .iter()
        .map(|entry| {
            let (label_lines, desc_lines) = wrapped(entry, &cols);
            label_lines.len().max(desc_lines.len())
        })
        .collect()
}

/// A skill's description led by its origin (`project · Deploy the app`); others as is.
fn description(entry: &CompletionEntry) -> String {
    match (entry.origin, entry.description.is_empty()) {
        (None, _) => entry.description.clone(),
        (Some(origin), true) => origin.label().to_owned(),
        (Some(origin), false) => format!("{}{ORIGIN_SEP}{}", origin.label(), entry.description),
    }
}

/// Wrapped label and description rows of one entry, shared by scroll math and drawing.
fn wrapped(entry: &CompletionEntry, cols: &Columns) -> (Vec<String>, Vec<String>) {
    (
        wrap_hard(display_label(&entry.label), cols.cmd),
        wrap_hard(&description(entry), cols.desc),
    )
}

/// Flatten entries into rendered terminal rows, wrapping label and description.
fn build_lines(app: &App, width: u16) -> Vec<VisualLine> {
    let entries = &app.completion.entries;
    if entries.is_empty() {
        return Vec::new();
    }
    let cols = Columns::new(width, entries);
    let mut out: Vec<VisualLine> = Vec::new();
    for (idx, entry) in entries.iter().enumerate() {
        let (label_lines, desc_lines) = wrapped(entry, &cols);
        for line_idx in 0..label_lines.len().max(desc_lines.len()) {
            let desc = desc_lines.get(line_idx).cloned().unwrap_or_default();
            let origin_chars = match entry.origin {
                Some(origin) if line_idx == 0 => origin.label().chars().count(),
                _ => 0,
            };
            out.push(VisualLine {
                label: label_lines.get(line_idx).cloned(),
                selected: idx == app.completion.selected,
                origin_chars: origin_chars.min(desc.chars().count()),
                desc,
            });
        }
    }
    out
}

fn display_label(label: &str) -> &str {
    label.strip_prefix('@').unwrap_or(label)
}

/// `(label, origin, description)` styles of a row.
fn row_styles(selected: bool) -> (Style, Style, Style) {
    let bg = Style::default().bg(theme::background());
    let label = bg.fg(theme::foreground()).add_modifier(Modifier::BOLD);
    let origin = bg.fg(theme::primary());
    if !selected {
        return (label, origin, theme::muted_style().bg(theme::background()));
    }
    // The selected command is `bold reverse`, its whole description italic;
    // a manual color swap would be a no-op under terminal-default ansi colors.
    (
        label.add_modifier(Modifier::REVERSED),
        origin.add_modifier(Modifier::ITALIC),
        bg.fg(theme::foreground()).add_modifier(Modifier::ITALIC),
    )
}

/// Draw the popup into `area` (its full bordered box).
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    if area.width < 8 || area.height < 3 {
        return;
    }
    let cols = Columns::new(area.width, &app.completion.entries);
    let cmd_w = cols.cmd;
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
    let desc_w = cols.desc;
    let scrollbar_x = desc_x + desc_w as u16;

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
        let (label_style, origin_style, desc_style) = row_styles(line.selected);
        let buf = f.buffer_mut();
        if let Some(label) = &line.label {
            buf.set_stringn(text_x, y, label, cmd_w, label_style);
        }
        let split = line
            .desc
            .char_indices()
            .nth(line.origin_chars)
            .map_or(line.desc.len(), |(at, _)| at);
        let (origin, rest) = line.desc.split_at(split);
        let (x, _) = buf.set_stringn(desc_x, y, origin, desc_w, origin_style);
        let used = (x - desc_x) as usize;
        buf.set_stringn(x, y, rest, desc_w.saturating_sub(used), desc_style);
    }

    // Scrollbar in the last content column when the list overflows the viewport.
    if total > inner.height as usize {
        let bar = Rect {
            x: scrollbar_x,
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

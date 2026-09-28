//! Option rows to wrapped visual lines, as Textual's `OptionList` folds them.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Span;

use crate::mcp::rows::{Row, SourceRow, ToolRow};
use crate::ui::theme;
use crate::ui::theme_picker::marker_green;

/// A styled character, the unit the wrapper works on.
type Sc = (char, Style);

/// One rendered line of an option row.
pub struct VisualLine {
    /// Index of the row this line belongs to, for click routing.
    pub row: usize,
    pub highlighted: bool,
    pub spans: Vec<Span<'static>>,
}

/// Wrap every row to `width`, in order, tagging the highlighted row's lines.
pub fn lines(rows: &[Row], selected: usize, width: usize) -> Vec<VisualLine> {
    let mut out = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let highlighted = index == selected && row.selectable();
        for chars in wrap(&styled(row, highlighted), width) {
            out.push(VisualLine {
                row: index,
                highlighted,
                spans: merge(&chars),
            });
        }
    }
    out
}

/// The styled characters of one row, before wrapping.
fn styled(row: &Row, highlighted: bool) -> Vec<Sc> {
    let bg = if highlighted {
        theme::block_cursor_bg()
    } else {
        theme::background()
    };
    let fg = if highlighted {
        theme::block_cursor_fg()
    } else {
        theme::foreground()
    };
    // The block cursor bolds the whole highlighted option, dim spans included.
    let mut base = Style::default().fg(fg).bg(bg);
    let mut dim = theme::dim(theme::muted()).bg(bg);
    if highlighted {
        base = base.add_modifier(Modifier::BOLD);
        dim = theme::dim(fg).bg(bg).add_modifier(Modifier::BOLD);
    }
    match row {
        Row::Header(title) => text(title, base.add_modifier(Modifier::BOLD)),
        Row::Blank => Vec::new(),
        Row::Note(note) => text(note, base),
        Row::Detail(detail) => text(detail, dim),
        Row::Hint { before, key, after } => {
            let mut chars = text(before, dim);
            chars.extend(text(key, base.fg(theme::primary())));
            chars.extend(text(after, dim));
            chars
        }
        Row::Source(source) => source_chars(source, base, dim, bg),
        Row::Tool(tool) => tool_chars(tool, base, dim),
    }
}

/// `  name  [transport]  n tools  ● status`, columns padded by the row builder.
fn source_chars(source: &SourceRow, base: Style, dim: Style, bg: Color) -> Vec<Sc> {
    // Column separators carry no modifier, so each span re-emits its own bold
    // and dim; ratatui's diff would otherwise drop bold after a dim span.
    let gap = Style::default().bg(bg);
    let symbol = if source.connected {
        base.fg(marker_green())
    } else {
        dim
    };
    let mut chars = text("  ", gap);
    chars.extend(text(&source.label, base));
    chars.extend(text("  ", gap));
    chars.extend(text(&source.transport, dim));
    chars.extend(text("  ", gap));
    chars.extend(text(&source.tools, dim));
    chars.extend(text("  ", gap));
    chars.extend(text(source.symbol, symbol));
    chars.extend(text(&format!(" {}", source.status), dim));
    chars
}

/// `name  -  description` in bold, or dim with a `(disabled)` tail when off.
fn tool_chars(tool: &ToolRow, base: Style, dim: Style) -> Vec<Sc> {
    let name_style = if tool.enabled {
        base.add_modifier(Modifier::BOLD)
    } else {
        dim
    };
    let mut chars = text(&tool.name, name_style);
    if !tool.description.is_empty() {
        let style = if tool.enabled { base } else { dim };
        chars.extend(text(&format!("  -  {}", tool.description), style));
    }
    if !tool.enabled {
        chars.extend(text("  (disabled)", dim.add_modifier(Modifier::ITALIC)));
    }
    chars
}

fn text(value: &str, style: Style) -> Vec<Sc> {
    value.chars().map(|character| (character, style)).collect()
}

/// Greedy word wrap keeping runs of spaces, so padded columns stay aligned.
/// The space run a break happens on is dropped, like Textual's fold.
fn wrap(chars: &[Sc], width: usize) -> Vec<Vec<Sc>> {
    let width = width.max(1);
    let mut rows: Vec<Vec<Sc>> = Vec::new();
    let mut row: Vec<Sc> = Vec::new();
    let mut spaces: Vec<Sc> = Vec::new();
    for word in words(chars) {
        let is_space = word.first().is_some_and(|(character, _)| *character == ' ');
        if is_space {
            spaces = word;
            continue;
        }
        if !row.is_empty() && row.len() + spaces.len() + word.len() > width {
            rows.push(std::mem::take(&mut row));
        } else {
            row.append(&mut spaces);
        }
        spaces.clear();
        row.extend(word);
    }
    row.append(&mut spaces);
    rows.push(row);
    rows
}

/// Split into alternating runs of spaces and non-spaces.
fn words(chars: &[Sc]) -> Vec<Vec<Sc>> {
    let mut words: Vec<Vec<Sc>> = Vec::new();
    for &(character, style) in chars {
        let space = character == ' ';
        match words.last_mut() {
            Some(word) if (word[0].0 == ' ') == space => word.push((character, style)),
            _ => words.push(vec![(character, style)]),
        }
    }
    words
}

/// Merge consecutive same-style characters into spans.
fn merge(chars: &[Sc]) -> Vec<Span<'static>> {
    let mut spans: Vec<Span<'static>> = Vec::new();
    let mut buffer = String::new();
    let mut style: Option<Style> = None;
    for &(character, current) in chars {
        if style != Some(current) {
            if let Some(previous) = style {
                spans.push(Span::styled(std::mem::take(&mut buffer), previous));
            }
            style = Some(current);
        }
        buffer.push(character);
    }
    if let Some(style) = style {
        spans.push(Span::styled(buffer, style));
    }
    spans
}

//! Option rows to wrapped visual lines.

use ratatui::style::{Color, Modifier, Style};

use crate::mcp::rows::{Row, SourceRow, ToolRow, MANAGE_CONNECTORS_LABEL};
use crate::mcp::scope::SHARED_ONLY_WARNING;
use crate::ui::visual_lines::{merge, text, wrap, Sc, VisualLine};
use crate::ui::{list_cursor, theme};

/// Wrap every row to `width`, in order, tagging the highlighted row's lines.
pub fn lines(rows: &[Row], selected: usize, width: usize) -> Vec<VisualLine> {
    let mut out = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let highlighted = index == selected && row.selectable();
        for chars in wrap(&styled(row, highlighted), width) {
            out.push(VisualLine {
                option: row.selectable().then_some(index),
                highlighted,
                spans: merge(&chars),
            });
        }
    }
    out
}

/// The styled characters of one row, before wrapping.
fn styled(row: &Row, highlighted: bool) -> Vec<Sc> {
    // The block cursor bolds the whole highlighted option, dim spans included.
    let (base, dim) = list_cursor::styles(highlighted);
    match row {
        Row::Header(title) => text(title, base.add_modifier(Modifier::BOLD)),
        Row::Blank => Vec::new(),
        Row::Note(note) => text(note, base),
        Row::Detail(detail) => text(detail, dim),
        Row::Hint { before, key, after } => {
            let mut chars = text(before, dim);
            chars.extend(text(key, list_cursor::accent(base, theme::primary())));
            chars.extend(text(after, dim));
            chars
        }
        Row::Source(source) => source_chars(source, base, dim, base.bg.unwrap_or_default()),
        Row::Tool(tool) => tool_chars(tool, base, dim),
        Row::Manage => text(
            MANAGE_CONNECTORS_LABEL,
            base.add_modifier(Modifier::UNDERLINED),
        ),
        Row::PrivateConnectorsHidden => text(
            SHARED_ONLY_WARNING,
            list_cursor::accent(base, theme::error()).add_modifier(Modifier::BOLD),
        ),
    }
}

/// `  name  [transport]  [plugin:x]  n tools  ● status`, columns padded by the row builder.
fn source_chars(source: &SourceRow, base: Style, dim: Style, bg: Color) -> Vec<Sc> {
    let gap = Style::default().bg(bg);
    let base = if source.blocked { dim } else { base };
    let symbol = if source.connected {
        list_cursor::accent(base, list_cursor::current_color())
    } else {
        dim
    };
    let mut chars = text("  ", gap);
    chars.extend(text(&source.label, base));
    chars.extend(text("  ", gap));
    chars.extend(text(&source.transport, dim));
    chars.extend(text("  ", gap));
    if !source.owner.is_empty() {
        chars.extend(text(&source.owner, dim));
        chars.extend(text("  ", gap));
    }
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

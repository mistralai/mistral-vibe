//! Option rows to wrapped visual lines; source and tool rows stay on one line.

use ratatui::style::{Color, Modifier, Style};
use unicode_width::UnicodeWidthChar;

use crate::mcp::rows::{Row, SourceRow, ToolRow, MANAGE_CONNECTORS_LABEL};
use crate::mcp::scope::SHARED_ONLY_WARNING;
use crate::ui::visual_lines::{columns, merge, text, wrap, Sc, VisualLine};
use crate::ui::{list_cursor, theme};

/// Wrap every row to `width`, in order, tagging the highlighted row's lines.
pub fn lines(rows: &[Row], selected: usize, width: usize) -> Vec<VisualLine> {
    let names = rows.iter().filter_map(|row| match row {
        Row::Tool(tool) => Some(columns(&text(&tool.name, Style::default()))),
        _ => None,
    });
    let name_column = names.max().unwrap_or(0).min(width * 2 / 5);
    let mut out = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let highlighted = index == selected && row.selectable();
        let chars = styled(row, highlighted, width, name_column);
        for chars in wrap(&chars, width) {
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
fn styled(row: &Row, highlighted: bool, width: usize, name_column: usize) -> Vec<Sc> {
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
        Row::Source(source) => {
            let bg = base.bg.unwrap_or_default();
            clip(source_chars(source, base, dim, bg, width), width)
        }
        Row::Tool(tool) => clip(tool_chars(tool, base, dim, name_column), width),
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
/// Too narrow, it drops `[transport]`, then the owner, then the tool count, then shortens the name.
fn source_chars(source: &SourceRow, base: Style, dim: Style, bg: Color, width: usize) -> Vec<Sc> {
    let gap = text("  ", Style::default().bg(bg));
    let base = if source.blocked { dim } else { base };
    let symbol = if source.connected {
        list_cursor::accent(base, list_cursor::current_color())
    } else {
        dim
    };
    let mut cells = vec![text(&source.label, base), text(&source.transport, dim)];
    if !source.owner.is_empty() {
        cells.push(text(&source.owner, dim));
    }
    cells.push(text(&source.tools, dim));
    let mut tail = text(source.symbol, symbol);
    tail.extend(text(&format!(" {}", source.status), dim));
    let fixed = |cells: &[Vec<Sc>]| cells.iter().map(|cell| columns(cell) + 2).sum::<usize>();
    while cells.len() > 1 && 2 + fixed(&cells) + columns(&tail) > width {
        cells.remove(1);
    }
    if cells.len() == 1 {
        let room = width.saturating_sub(4 + columns(&tail));
        cells[0] = fit(std::mem::take(&mut cells[0]), room);
    }
    let mut chars = gap.clone();
    for cell in cells {
        chars.extend(cell);
        chars.extend(gap.iter().copied());
    }
    chars.extend(tail);
    chars
}

/// A padded cell shrunk to `room` columns: padding goes first, then the text ends on `…`.
fn fit(mut cell: Vec<Sc>, room: usize) -> Vec<Sc> {
    let Some(&(_, style)) = cell.first() else {
        return cell;
    };
    while columns(&cell) > room && cell.last().is_some_and(|(character, _)| *character == ' ') {
        cell.pop();
    }
    let mut cell = clip(cell, room);
    cell.extend(std::iter::repeat_n(
        (' ', style),
        room.saturating_sub(columns(&cell)),
    ));
    cell
}

/// Cut to `width` columns, ending on `…` styled like the text it cuts; cutting only blanks adds none.
pub(super) fn clip(mut chars: Vec<Sc>, width: usize) -> Vec<Sc> {
    if columns(&chars) <= width {
        return chars;
    }
    if width == 0 {
        return Vec::new();
    }
    while chars.last().is_some_and(|(character, _)| *character == ' ') {
        chars.pop();
    }
    if columns(&chars) <= width {
        return chars;
    }
    let mut kept = Vec::new();
    let mut used = 0;
    for sc in chars {
        let cell = sc.0.width().unwrap_or(0);
        if used + cell + 1 > width {
            break;
        }
        used += cell;
        kept.push(sc);
    }
    while kept.last().is_some_and(|(character, _)| *character == ' ') {
        kept.pop();
    }
    let style = kept.last().map_or_else(Style::default, |(_, style)| *style);
    kept.push(('…', style));
    kept
}

/// `name  description`: the bold name padded to `name_column` (a longer one stays whole), then the dim description.
/// A disabled tool has a dim name and an italic `(disabled)` before its description.
fn tool_chars(tool: &ToolRow, base: Style, dim: Style, name_column: usize) -> Vec<Sc> {
    let name_style = if tool.enabled {
        base.add_modifier(Modifier::BOLD)
    } else {
        dim
    };
    let mut rest = Vec::new();
    if !tool.enabled {
        rest.extend(text("(disabled)", dim.add_modifier(Modifier::ITALIC)));
    }
    if !tool.description.is_empty() {
        if !rest.is_empty() {
            rest.extend(text(" ", dim));
        }
        rest.extend(text(&tool.description, dim));
    }
    if rest.is_empty() {
        return text(&tool.name, name_style);
    }
    let mut chars = text(&tool.name, name_style);
    let pad = name_column.saturating_sub(columns(&chars)) + 2;
    chars.extend(text(&" ".repeat(pad), dim));
    chars.extend(rest);
    chars
}

//! Pipe tables, matching Textual's grid + `keyline` border cell-for-cell.

use std::sync::Arc;

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::Line;

use super::text::{cell_width, merge, pad_line, wrap_chars};
use super::{Out, Sc, TableCell, TableCellSpan};
use crate::ui::theme;

/// Render a table with a keyline rule between every grid row.
pub(super) fn render_table(
    headers: &[Vec<Sc>],
    rows: &[Vec<Vec<Sc>>],
    width: u16,
    pad: usize,
    out: &mut Out,
) {
    let n = headers.len();
    if n == 0 {
        return;
    }
    let table = out.table;
    let widths = super::table_layout::column_widths(headers, rows, width, pad);
    out.lines.push(rule('┌', '┬', '┐', &widths, pad));
    render_row(headers, &widths, true, table, 0, pad, out);
    for (index, r) in rows.iter().enumerate() {
        out.lines.push(rule('├', '┼', '┤', &widths, pad));
        render_row(r, &widths, false, table, index + 1, pad, out);
    }
    out.lines.push(rule('└', '┴', '┘', &widths, pad));
}

/// A border rule: `left`, then `fill` runs joined by `mid`, closed by `right`.
fn rule(left: char, mid: char, right: char, widths: &[usize], pad: usize) -> Line<'static> {
    let mut cells: Vec<Sc> = vec![(left, border())];
    for (i, &w) in widths.iter().enumerate() {
        cells.extend(std::iter::repeat_n(('─', border()), w));
        cells.push((if i + 1 == widths.len() { right } else { mid }, border()));
    }
    pad_line(merge(&cells), pad)
}

/// A logical row: equally tall wrapped cells with one column of horizontal padding.
fn render_row(
    cells: &[Vec<Sc>],
    widths: &[usize],
    header: bool,
    table: usize,
    row: usize,
    pad: usize,
    out: &mut Out,
) {
    let empty = Vec::new();
    let fill = if header {
        Style::default().fg(theme::primary())
    } else {
        Style::default()
    };
    let wrapped: Vec<Vec<Vec<Sc>>> = widths
        .iter()
        .enumerate()
        .map(|(column, width)| {
            let region = width.saturating_sub(2);
            match region {
                0 => vec![Vec::new()],
                _ => wrap_chars(cells.get(column).unwrap_or(&empty), region),
            }
        })
        .collect();
    let height = wrapped.iter().map(Vec::len).max().unwrap_or(1);
    let y = out.lines.len() as u16;
    let mut x = pad + 1;
    for (column, (&width, lines)) in widths.iter().zip(&wrapped).enumerate() {
        let text: Arc<str> = cells
            .get(column)
            .unwrap_or(&empty)
            .iter()
            .map(|(character, _)| character)
            .collect::<String>()
            .into();
        let spans = cell_spans(lines, cells.get(column).unwrap_or(&empty), x, y);
        out.cells.push(TableCell {
            table,
            row,
            column,
            area: Rect::new(x as u16, y, width as u16, height as u16),
            spans,
            text,
        });
        x += width + 1;
    }
    for line in 0..height {
        let mut rendered: Vec<Sc> = vec![('│', border())];
        for (&width, lines) in widths.iter().zip(&wrapped) {
            let region = width.saturating_sub(2);
            let cell = lines.get(line).map(Vec::as_slice).unwrap_or_default();
            if width > 0 {
                rendered.push((' ', Style::default()));
            }
            for &(character, style) in cell {
                rendered.push((character, if header { fill } else { style }));
            }
            for _ in cell_width(cell)..region {
                rendered.push((' ', fill));
            }
            if width > 1 {
                rendered.push((' ', Style::default()));
            }
            rendered.push(('│', border()));
        }
        out.lines.push(pad_line(merge(&rendered), pad));
    }
}

fn cell_spans(lines: &[Vec<Sc>], text: &[Sc], x: usize, y: u16) -> Vec<TableCellSpan> {
    let mut text_offset = 0;
    lines
        .iter()
        .enumerate()
        .filter_map(|(line, content)| {
            while text_offset < text.len()
                && text[text_offset].0 == ' '
                && content.first() != Some(&text[text_offset])
            {
                text_offset += 1;
            }
            if content.is_empty() {
                return None;
            }
            let text_start = text_offset;
            text_offset += content.len();
            let span = TableCellSpan {
                y: y + line as u16,
                x0: (x + 1) as u16,
                x1: (x + cell_width(content)) as u16,
                text_start,
                text_end: text_offset,
            };
            while text_offset < text.len() && text[text_offset].0 == ' ' {
                text_offset += 1;
            }
            Some(span)
        })
        .collect()
}

fn border() -> Style {
    Style::default().fg(theme::md_table_border())
}

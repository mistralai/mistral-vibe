//! Styled characters wrapped into the option lines the browser boxes draw.

use ratatui::style::Style;
use ratatui::text::Span;
use unicode_width::UnicodeWidthChar;

/// A styled character, the unit the wrapper works on.
pub(crate) type Sc = (char, Style);

/// One rendered line of an option row.
pub struct VisualLine {
    /// The selectable row this line belongs to, for click routing.
    pub option: Option<usize>,
    pub highlighted: bool,
    pub spans: Vec<Span<'static>>,
}

/// Keeps `\n` for `wrap` to break on; other control characters never render.
pub(crate) fn text(value: &str, style: Style) -> Vec<Sc> {
    value
        .chars()
        .filter(|character| *character == '\n' || !character.is_control())
        .map(|character| (character, style))
        .collect()
}

/// Hard-break on `\n` as Rich does, then word-wrap each line.
pub(crate) fn wrap(chars: &[Sc], width: usize) -> Vec<Vec<Sc>> {
    chars
        .split(|(character, _)| *character == '\n')
        .flat_map(|line| wrap_line(line, width))
        .collect()
}

/// Greedy word wrap keeping runs of spaces, so padded columns stay aligned; the run a line breaks on is dropped.
fn wrap_line(chars: &[Sc], width: usize) -> Vec<Vec<Sc>> {
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
        if !row.is_empty() && columns(&row) + columns(&spaces) + columns(&word) > width {
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

/// Terminal columns a run occupies, so wide glyphs wrap by the cells they fill.
fn columns(chars: &[Sc]) -> usize {
    chars
        .iter()
        .map(|(character, _)| character.width().unwrap_or(0))
        .sum()
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

/// Wrap with a hanging indent, folding words longer than a line (Rich `overflow=fold`); an indent deeper than half the line is cut to half, so text always shows.
pub(crate) fn wrap_indented(line: &str, style: Style, width: usize) -> Vec<Vec<Sc>> {
    let body = line.trim_start_matches(' ');
    let depth = (line.len() - body.len()).min(width / 2);
    let indent = text(&" ".repeat(depth), style);
    let room = width.saturating_sub(depth).max(1);
    wrap(&text(body, style), room)
        .into_iter()
        .flat_map(|row| fold(row, room))
        .map(|row| [indent.clone(), row].concat())
        .collect()
}

/// Split a row into chunks of at most `room` terminal columns, so wide glyphs fold too.
fn fold(row: Vec<Sc>, room: usize) -> Vec<Vec<Sc>> {
    let mut out = vec![Vec::new()];
    let mut used = 0;
    for sc in row {
        let width = sc.0.width().unwrap_or(0);
        if used + width > room && used > 0 {
            out.push(Vec::new());
            used = 0;
        }
        if let Some(chunk) = out.last_mut() {
            chunk.push(sc);
        }
        used += width;
    }
    out
}

/// Merge consecutive same-style characters into spans.
pub(crate) fn merge(chars: &[Sc]) -> Vec<Span<'static>> {
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

#[cfg(test)]
mod tests;

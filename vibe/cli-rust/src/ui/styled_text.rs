//! Cell-aware wrapping for styled terminal text.

use ratatui::style::Style;
use ratatui::text::Span;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

pub(crate) fn width(spans: &[Span<'static>]) -> usize {
    spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect::<String>()
        .width()
}

pub(crate) fn wrap_hard(spans: &[Span<'static>], width: usize) -> Vec<Vec<Span<'static>>> {
    let width = width.max(1);
    let chars = styled_chars(spans);
    let text = chars
        .iter()
        .map(|(character, _)| character)
        .collect::<String>();
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut used = 0;
    let mut char_index = 0;
    for grapheme in text.graphemes(true) {
        let cell = grapheme.width();
        if !row.is_empty() && used + cell > width {
            rows.push(std::mem::take(&mut row));
            used = 0;
        }
        let end = char_index + grapheme.chars().count();
        for &(character, style) in &chars[char_index..end] {
            push(&mut row, character, style);
        }
        used += cell;
        char_index = end;
    }
    rows.push(row);
    rows
}

pub(crate) fn split_at_width(
    spans: &[Span<'static>],
    width: usize,
) -> (Vec<Span<'static>>, Vec<Span<'static>>) {
    let chars = styled_chars(spans);
    let text = chars
        .iter()
        .map(|(character, _)| character)
        .collect::<String>();
    let mut used = 0;
    let mut split = chars.len();
    let mut char_index = 0;
    for grapheme in text.graphemes(true) {
        let cell = grapheme.width();
        if char_index > 0 && used + cell > width {
            split = char_index;
            break;
        }
        used += cell;
        char_index += grapheme.chars().count();
    }
    (merge(&chars[..split]), merge(&chars[split..]))
}

fn styled_chars(spans: &[Span<'static>]) -> Vec<(char, Style)> {
    spans
        .iter()
        .flat_map(|span| {
            span.content
                .chars()
                .map(move |character| (character, span.style))
        })
        .collect()
}

fn merge(chars: &[(char, Style)]) -> Vec<Span<'static>> {
    let mut spans = Vec::new();
    for &(character, style) in chars {
        push(&mut spans, character, style);
    }
    spans
}

fn push(spans: &mut Vec<Span<'static>>, character: char, style: Style) {
    if let Some(last) = spans.last_mut().filter(|span| span.style == style) {
        last.content.to_mut().push(character);
        return;
    }
    spans.push(Span::styled(character.to_string(), style));
}

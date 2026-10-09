//! Cell-aware wrapping for styled terminal text.

use ratatui::style::Style;
use ratatui::text::Span;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::markdown::{wrap_chars, Sc};

/// Word-wrap like assistant prose: only a word wider than the row is cut inside.
pub(crate) fn wrap_hard(spans: &[Span<'static>], width: usize) -> Vec<Vec<Span<'static>>> {
    let chars: Vec<Sc> = spans
        .iter()
        .flat_map(|span| {
            span.content
                .chars()
                .map(move |character| (character, span.style, None))
        })
        .collect();
    wrap_chars(&chars, width)
        .into_iter()
        .map(|row| {
            let mut spans = Vec::new();
            for (character, style, _) in row {
                push(&mut spans, character, style);
            }
            spans
        })
        .collect()
}

/// [`wrap_hard`], pairing each row with its fold gap.
pub(crate) fn wrap_hard_folded(
    spans: &[Span<'static>],
    width: usize,
) -> Vec<(Vec<Span<'static>>, Option<u16>)> {
    let text = |spans: &[Span<'static>]| {
        spans
            .iter()
            .map(|span| span.content.as_ref())
            .collect::<String>()
    };
    crate::selection::fold::paired(&text(spans), wrap_hard(spans, width), |row| text(row))
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

#[cfg(test)]
mod tests;

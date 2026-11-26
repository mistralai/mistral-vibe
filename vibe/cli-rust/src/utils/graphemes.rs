//! Graphemes as drawn: zero-width ones join a visible neighbour, like Rich's `split_graphemes`.

use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

fn is_control(grapheme: &str) -> bool {
    grapheme.starts_with(char::is_control)
}

/// `(byte, grapheme)` pairs; a zero-width grapheme joins the previous one (a tab too), or the next at the start.
pub fn split_graphemes(text: &str) -> impl Iterator<Item = (usize, &str)> {
    let mut graphemes = text.grapheme_indices(true).peekable();
    std::iter::from_fn(move || {
        let (start, first) = graphemes.next()?;
        let mut end = start + first.len();
        let mut visible = first.width() > 0;
        if first == "\t" || !is_control(first) {
            while let Some((_, next)) =
                graphemes.next_if(|&(_, next)| !is_control(next) && (!visible || next.width() == 0))
            {
                end += next.len();
                visible |= next.width() > 0;
            }
        }
        Some((start, &text[start..end]))
    })
}

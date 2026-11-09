//! The prompt text of a user message, and the pinned copy drawn over the transcript top.

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::super::super::markdown::{self, LinkedLines, Sc};
use super::super::super::theme;
use crate::collapsed_pastes::Collapsed;
use crate::selection::Fold;
use crate::server::MessageEntry;
use crate::transcript::{split_loop_prompt, FiredLoop};
use crate::utils::text;

/// The prompt text behind its `> ` marker, cut to `max_rows` with a trailing `…`.
pub(super) fn push(
    lines: &mut LinkedLines,
    message: &MessageEntry,
    width: u16,
    (prompt, content): (Style, Style),
    padded: bool,
    max_rows: usize,
) -> Collapsed {
    let content_width = width.saturating_sub(2) as usize;
    let collapsed = crate::collapsed_pastes::collapse_marked(
        &super::message_text(message),
        message.user_display_content.as_ref(),
    );
    let placeholder = content.fg(theme::text_primary());
    // Graphemes past the rows kept are never wrapped.
    let budget = max_rows
        .saturating_add(1)
        .saturating_mul(content_width.max(1));
    let mut rows: Vec<(Vec<Sc>, Option<u16>, bool)> = Vec::new();
    let mut cut = false;
    for (index, segments) in collapsed.lines().enumerate() {
        let mut body = String::new();
        let mut chars: Vec<Sc> = Vec::new();
        let mut cells = 0;
        for (segment, is_placeholder) in segments {
            let expanded = text::expand_tabs(&format!("{body}{segment}"));
            let segment = &expanded[body.len()..];
            let style = match is_placeholder {
                true => placeholder,
                false => content,
            };
            for grapheme in segment.graphemes(true) {
                cells += grapheme.width();
                if cells > budget {
                    cut = true;
                    break;
                }
                chars.extend(grapheme.chars().map(|c| (c, style, None)));
            }
            if cut {
                break;
            }
            body.push_str(segment);
        }
        // Continuation rows hang under the text, not under the prompt marker.
        for (row_index, (row, gap)) in markdown::wrap_chars_folded(&chars, content_width)
            .into_iter()
            .enumerate()
        {
            rows.push((row, gap, index == 0 && row_index == 0));
        }
        if cut || rows.len() > max_rows {
            break;
        }
    }
    let cut = cut || rows.len() > max_rows;
    rows.truncate(max_rows);
    if let Some((row, _, _)) = rows.last_mut().filter(|_| cut) {
        while !row.is_empty() && markdown::cell_width(row) + 1 > content_width {
            row.pop();
        }
        row.push(('…', content, None));
    }
    for (mut row, gap, first) in rows {
        let marker = match first {
            true => "> ",
            false => "  ",
        };
        if padded {
            let pad = content_width.saturating_sub(markdown::cell_width(&row));
            row.extend(std::iter::repeat_n((' ', content, None), pad));
        }
        lines.push_row(vec![Span::styled(marker, prompt)], &row);
        lines.fold_last(Fold::hung(gap, 2));
    }
    collapsed
}

/// The pinned prompt: its transcript row cut to `max_rows` (a loop pins as `⏵ title`), then the separator.
pub(in crate::ui::transcript) fn sticky(
    message: &MessageEntry,
    fired_loop: Option<&FiredLoop>,
    width: u16,
    max_rows: usize,
) -> LinkedLines {
    let mut lines = LinkedLines::default();
    let styles = super::user::prompt_styles(fired_loop.is_some(), false, false);
    match fired_loop {
        Some(fired) => push_loop_title(&mut lines, message, fired, width, styles, max_rows),
        None => {
            push(&mut lines, message, width, styles, false, max_rows);
        }
    }
    super::user::push_separator(&mut lines, width);
    lines
}

/// A loop prompt's folded header, as the transcript draws it, cut to `max_rows` with a trailing `…`.
fn push_loop_title(
    lines: &mut LinkedLines,
    message: &MessageEntry,
    fired: &FiredLoop,
    width: u16,
    styles: (Style, Style),
    max_rows: usize,
) {
    let content_width = width.saturating_sub(2) as usize;
    let text = super::message_text(message);
    let (title, _) = split_loop_prompt(&text);
    // Graphemes past the rows kept are never wrapped.
    let budget = max_rows
        .saturating_add(1)
        .saturating_mul(content_width.max(1));
    let mut kept = String::new();
    let mut cells = 0;
    let mut cut = false;
    for grapheme in title.graphemes(true) {
        cells += grapheme.width();
        if cells > budget {
            cut = true;
            break;
        }
        kept.push_str(grapheme);
    }
    let mut header = LinkedLines::default();
    super::loop_prompt::push_loop_prompt(&mut header, &kept, fired, false, styles, content_width);
    let cut = cut || header.len() > max_rows;
    header.retain(|index, _| index < max_rows);
    if cut {
        if let Some(last) = header.lines_mut().last_mut() {
            ellipsize_line(last, usize::from(width), styles.1);
        }
    }
    lines.append(header);
}

/// End `line` with `…` within `width` cells, dropping graphemes from its end to make room.
fn ellipsize_line(line: &mut Line<'static>, width: usize, style: Style) {
    while line.width() + 1 > width {
        let Some(span) = line.spans.last_mut() else {
            break;
        };
        let mut content = span.content.to_string();
        let Some((at, _)) = content.grapheme_indices(true).next_back() else {
            line.spans.pop();
            continue;
        };
        content.truncate(at);
        span.content = content.into();
    }
    line.spans.push(Span::styled("…", style));
}

#[cfg(test)]
mod tests;

//! A prompt a scheduled loop sent: its title, folding its instructions and loop line.

use ratatui::style::Style;
use ratatui::text::{Line, Span};

use super::expand_marker;
use crate::selection::Fold;
use crate::transcript::{split_loop_prompt, FiredLoop};
use crate::ui::markdown::{self, LinkedLines, Sc};
use crate::ui::theme;
use crate::utils::{datetime, text};

/// `⏵ title`; unfolded, the instructions and `loop {id} - {fired at}` hang under a `│`/`└` border.
pub(super) fn push_loop_prompt(
    lines: &mut LinkedLines,
    prompt: &str,
    fired: &FiredLoop,
    expanded: bool,
    (marker_style, title_style): (Style, Style),
    width: usize,
) {
    let (title, body) = split_loop_prompt(prompt);
    let mut header = wrapped(title, title_style, width);
    let marker = format!("{} ", expand_marker(expanded));
    header.prefix(|index| match index {
        0 => Span::styled(marker.clone(), marker_style),
        _ => Span::raw("  "),
    });
    lines.append(header);
    if !expanded {
        return;
    }
    let muted = theme::muted_style();
    let mut content = LinkedLines::default();
    for line in body.lines() {
        content.append(wrapped(line, muted, width));
    }
    push_fired_loop(&mut content, fired);
    let last = content.len() - 1;
    content.prefix(|index| Span::styled(if index == last { "└ " } else { "│ " }, muted));
    lines.append(content);
}

/// `text` wrapped to `width`; an empty line keeps its row.
fn wrapped(text: &str, style: Style, width: usize) -> LinkedLines {
    let mut lines = LinkedLines::default();
    let chars: Vec<Sc> = text::expand_tabs(text)
        .chars()
        .map(|c| (c, style, None))
        .collect();
    let rows = markdown::wrap_chars_folded(&chars, width);
    if rows.is_empty() {
        lines.push(Line::default());
    }
    for (row, gap) in rows {
        lines.push_row(Vec::new(), &row);
        lines.fold_last(Fold::hung(gap, 0));
    }
    lines
}

fn push_fired_loop(lines: &mut LinkedLines, fired: &FiredLoop) {
    let muted = theme::muted_style();
    let mut spans = vec![
        Span::styled("loop ", muted),
        Span::styled(fired.loop_id.clone(), theme::text(theme::primary())),
    ];
    if let Some(fired_at) = fired.fired_at {
        spans.push(Span::styled(" - ", muted));
        spans.push(Span::styled(
            datetime::format_local(fired_at),
            theme::dim(theme::foreground()),
        ));
    }
    lines.push(Line::from(spans));
}

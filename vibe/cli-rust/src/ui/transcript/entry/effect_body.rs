//! Effect result bodies: warnings, code blocks, todos, and the edit diff view.

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::super::super::markdown::{LinkKind, LinkedLines, Sc};
use super::super::super::{highlight, theme};
use super::super::diff;
use super::bordered::{body_width, prefix, BORDER_WIDTH};
use crate::selection::{fold, Fold};
use crate::server::effect_output::{BodyLine, TodoRow};
use crate::utils::text;

/// Paint diff rows under the effect header: expanding border, then the banded, wrapped row.
pub(super) fn push_edit_diff(
    lines: &mut LinkedLines,
    rows: &[diff::DiffRow],
    width: u16,
    warnings: &[String],
) {
    let content_width = body_width(width);
    // Python composes the warnings into the same result widget as the diff, so
    // they join the diff's border run and shift its closing glyph down.
    let warning_rows = wrap_warnings(warnings, content_width);
    let mut rendered = Vec::new();
    for row in rows {
        for (spans, gap) in diff::wrap_row(row, content_width) {
            let fold = Fold::hung(gap, BORDER_WIDTH + row.gutter_width);
            rendered.push((row.border, row.band, spans, fold));
        }
    }
    let last = (warning_rows.len() + rendered.len()).saturating_sub(1);
    push_warning_rows(lines, &warning_rows, last);
    for (index, (border, band, row, fold)) in rendered.into_iter().enumerate() {
        let mut spans = vec![prefix(warning_rows.len() + index == last, border)];
        spans.extend(diff::banded(&row, band, content_width));
        lines.push_folded(Line::from(spans), fold);
    }
}

/// One wrapped body row: a row of a led line (web-search source), warning, or highlightable text.
enum Row {
    /// The lead (or its blank hang on wrapped rows), the text, and the link it opens.
    Led(String, String, Option<usize>),
    Warning(String),
    Text(String),
}

/// The bordered result body: warnings stacked above the content rows
/// (Python's Read/Edit/Grep widgets compose them into one result widget).
pub(super) fn push_effect_body(
    lines: &mut LinkedLines,
    body: &[BodyLine],
    width: u16,
    content_style: Style,
    lang: &str,
    warnings: &[String],
) {
    let content = body_width(width) as usize;
    let rows = wrap_warnings(warnings, body_width(width))
        .into_iter()
        .map(|(row, gap)| (Row::Warning(row), Fold::hung(gap, BORDER_WIDTH)))
        .chain(body.iter().flat_map(|line| {
            let link = line
                .link
                .clone()
                .map(|url| lines.link(url, LinkKind::External));
            let text = text::expand_tabs(&line.text);
            if line.lead.is_empty() && link.is_none() {
                return fold::wrap_hard(&text, content)
                    .into_iter()
                    .map(|(row, gap)| (Row::Text(row), Fold::hung(gap, BORDER_WIDTH)))
                    .collect::<Vec<_>>();
            }
            // Wrapped rows hang under the text, unless the body is too narrow to indent.
            let (lead, text, hang) = match line.lead.width() {
                hang if hang < content => (line.lead, text, hang),
                _ => ("", format!("{}{text}", line.lead), 0),
            };
            fold::wrap_hard(&text, content - hang)
                .into_iter()
                .enumerate()
                .map(|(index, (row, gap))| {
                    let lead = match index {
                        0 => lead.to_owned(),
                        _ => " ".repeat(hang),
                    };
                    (
                        Row::Led(lead, row, link),
                        Fold::hung(gap, BORDER_WIDTH + hang as u16),
                    )
                })
                .collect()
        }))
        .collect::<Vec<_>>();
    let last = rows.len().saturating_sub(1);
    for (i, (row, fold)) in rows.iter().enumerate() {
        let style = theme::muted_style();
        let mut spans = vec![prefix(i == last, style)];
        match row {
            Row::Warning(text_line) => {
                spans.push(Span::styled(
                    text_line.to_string(),
                    theme::text(theme::warning()),
                ));
            }
            Row::Led(lead, text_line, link) => {
                if !lead.is_empty() {
                    spans.push(Span::styled(lead.clone(), style));
                }
                let Some(link) = link else {
                    spans.push(Span::styled(text_line.clone(), content_style));
                    lines.push_folded(Line::from(spans), *fold);
                    continue;
                };
                let link_style = theme::text(theme::md_link())
                    .add_modifier(Modifier::DIM)
                    .add_modifier(Modifier::UNDERLINED);
                let title: Vec<Sc> = text_line
                    .chars()
                    .map(|c| (c, link_style, Some(*link)))
                    .collect();
                lines.push_row(spans, &title);
                lines.fold_last(*fold);
                continue;
            }
            Row::Text(text_line) => {
                match highlight::code(text_line, lang).and_then(|rows| rows.into_iter().next()) {
                    Some(highlighted) => spans.extend(highlighted),
                    None => spans.push(Span::styled(text_line.to_string(), content_style)),
                }
            }
        }
        lines.push_folded(Line::from(spans), *fold);
    }
}

/// Python `TodoResultWidget`: one `{icon} {content}` row per todo, bucketed by
/// status and colored per bucket (`.todo-{status}` classes).
pub(super) fn push_todo_body(lines: &mut LinkedLines, rows: &[TodoRow<'_>], width: u16) {
    let wrapped = rows
        .iter()
        .flat_map(|row| {
            fold::wrap_hard(&text::expand_tabs(&row.text), body_width(width) as usize)
                .into_iter()
                .zip(std::iter::repeat(row.status))
        })
        .collect::<Vec<_>>();
    let last = wrapped.len().saturating_sub(1);
    for (i, ((text_line, gap), status)) in wrapped.iter().enumerate() {
        let line = Line::from(vec![
            prefix(i == last, theme::muted_style()),
            Span::styled(text_line.to_string(), todo_style(status)),
        ]);
        lines.push_folded(line, Fold::hung(*gap, BORDER_WIDTH));
    }
}

fn todo_style(status: &str) -> Style {
    match status {
        "in_progress" => theme::text(theme::warning()),
        "pending" => theme::text(theme::foreground()),
        "completed" => theme::text(theme::success()),
        // cancelled shares the empty list's muted style (`.todo-empty`).
        _ => theme::muted_style(),
    }
}

fn wrap_warnings(warnings: &[String], width: u16) -> Vec<(String, Option<u16>)> {
    warnings
        .iter()
        .flat_map(|warning| {
            // Python stacks each result warning as `⚠ {warning}` (`.tool-result-warning`).
            fold::wrap_hard(&text::expand_tabs(&format!("⚠ {warning}")), width as usize)
        })
        .collect()
}

fn push_warning_rows(lines: &mut LinkedLines, rows: &[(String, Option<u16>)], last: usize) {
    for (index, (row, gap)) in rows.iter().enumerate() {
        let line = Line::from(vec![
            prefix(index == last, theme::muted_style()),
            Span::styled(row.to_string(), theme::text(theme::warning())),
        ]);
        lines.push_folded(line, Fold::hung(*gap, BORDER_WIDTH));
    }
}

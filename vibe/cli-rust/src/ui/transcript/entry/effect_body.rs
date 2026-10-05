//! Effect result bodies: warnings, code blocks, todos, and the edit diff view.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use super::super::super::markdown::{LinkKind, LinkedLines, Sc};
use super::super::super::{highlight, styled_text, theme};
use super::super::diff;
use super::bordered::{body_width, prefix};
use crate::server::effect_output::{BodyLine, TodoRow};
use crate::utils::text;

/// Paint diff rows under the effect header: expanding border, then the banded row.
/// Rows wrap instead of overflowing: the gutter repeats on continuation rows so
/// wrapped content keeps its structure and nothing scrolls horizontally.
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
        let (gutter, body) = styled_text::split_at_width(&row.spans, row.gutter_width as usize);
        let line_width = content_width.saturating_sub(row.gutter_width).max(1) as usize;
        for (index, body) in styled_text::wrap_hard(&body, line_width)
            .into_iter()
            .enumerate()
        {
            let mut spans = if index == 0 {
                gutter.clone()
            } else {
                vec![Span::raw(" ".repeat(row.gutter_width as usize))]
            };
            spans.extend(body);
            rendered.push((row.border, row.band, spans));
        }
    }
    let last = (warning_rows.len() + rendered.len()).saturating_sub(1);
    push_warning_rows(lines, &warning_rows, last);
    for (index, (border, band, row)) in rendered.into_iter().enumerate() {
        let mut spans = vec![prefix(warning_rows.len() + index == last, border)];
        spans.extend(banded(&row, band, content_width));
        lines.push(Line::from(spans));
    }
}

/// Clip a row to the diff view's width and paint its band across the whole of it.
fn banded(spans: &[Span<'static>], band: Option<Color>, width: u16) -> Vec<Span<'static>> {
    let mut out = Vec::with_capacity(spans.len() + 1);
    let mut used = 0usize;
    for span in spans {
        let room = (width as usize).saturating_sub(used);
        if room == 0 {
            break;
        }
        let content = truncate(&span.content, room);
        used += content.width();
        let style = match band {
            Some(color) => span.style.bg(color),
            None => span.style,
        };
        out.push(Span::styled(content, style));
    }
    if let Some(color) = band {
        let padding = (width as usize).saturating_sub(used);
        if padding > 0 {
            out.push(Span::styled(
                " ".repeat(padding),
                Style::default().bg(color),
            ));
        }
    }
    out
}

/// Cut to `width` cells, so a wide glyph never straddles the band's edge.
fn truncate(text: &str, width: usize) -> String {
    if text.width() <= width {
        return text.to_owned();
    }
    let mut out = String::new();
    let mut used = 0;
    for c in text.chars() {
        let cell = c.width().unwrap_or(0);
        if used + cell > width {
            break;
        }
        out.push(c);
        used += cell;
    }
    out
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
        .map(Row::Warning)
        .chain(body.iter().flat_map(|line| {
            let link = line
                .link
                .clone()
                .map(|url| lines.link(url, LinkKind::External));
            let text = text::expand_tabs(&line.text);
            if line.lead.is_empty() && link.is_none() {
                return text::wrap_hard(&text, content)
                    .into_iter()
                    .map(Row::Text)
                    .collect::<Vec<_>>();
            }
            // Wrapped rows hang under the text, unless the body is too narrow to indent.
            let (lead, text, hang) = match line.lead.width() {
                hang if hang < content => (line.lead, text, hang),
                _ => ("", format!("{}{text}", line.lead), 0),
            };
            text::wrap_hard(&text, content - hang)
                .into_iter()
                .enumerate()
                .map(|(index, row)| {
                    let lead = match index {
                        0 => lead.to_owned(),
                        _ => " ".repeat(hang),
                    };
                    Row::Led(lead, row, link)
                })
                .collect()
        }))
        .collect::<Vec<_>>();
    let last = rows.len().saturating_sub(1);
    for (i, row) in rows.iter().enumerate() {
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
                    lines.push(Line::from(spans));
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
                continue;
            }
            Row::Text(text_line) => {
                match highlight::code(text_line, lang).and_then(|rows| rows.into_iter().next()) {
                    Some(highlighted) => spans.extend(highlighted),
                    None => spans.push(Span::styled(text_line.to_string(), content_style)),
                }
            }
        }
        lines.push(Line::from(spans));
    }
}

/// Python `TodoResultWidget`: one `{icon} {content}` row per todo, bucketed by
/// status and colored per bucket (`.todo-{status}` classes).
pub(super) fn push_todo_body(lines: &mut LinkedLines, rows: &[TodoRow<'_>], width: u16) {
    let wrapped = rows
        .iter()
        .flat_map(|row| {
            text::wrap_hard(&text::expand_tabs(&row.text), body_width(width) as usize)
                .into_iter()
                .zip(std::iter::repeat(row.status))
        })
        .collect::<Vec<_>>();
    let last = wrapped.len().saturating_sub(1);
    for (i, (text_line, status)) in wrapped.iter().enumerate() {
        lines.push(Line::from(vec![
            prefix(i == last, theme::muted_style()),
            Span::styled(text_line.to_string(), todo_style(status)),
        ]));
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

fn wrap_warnings(warnings: &[String], width: u16) -> Vec<String> {
    warnings
        .iter()
        .flat_map(|warning| {
            // Python stacks each result warning as `⚠ {warning}` (`.tool-result-warning`).
            text::wrap_hard(&text::expand_tabs(&format!("⚠ {warning}")), width as usize)
        })
        .collect()
}

fn push_warning_rows(lines: &mut LinkedLines, rows: &[String], last: usize) {
    for (index, row) in rows.iter().enumerate() {
        lines.push(Line::from(vec![
            prefix(index == last, theme::muted_style()),
            Span::styled(row.to_string(), theme::text(theme::warning())),
        ]));
    }
}

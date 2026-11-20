//! The body pane's lines: a page's or picker's skill body rendered as markdown, or why it is not there.

use ratatui::text::Span;

use crate::skills_browser::labels;
use crate::skills_browser::model::Body;
use crate::ui::list_cursor;
use crate::ui::markdown;
use crate::ui::visual_lines::{merge, wrap_indented, VisualLine};
use crate::utils::text::expand_tabs;

/// The columns every pane line starts with.
const INDENT: &str = "  ";

/// The pane's lines, `width` wide.
pub(crate) fn wrap(body: &Body, width: usize) -> Vec<VisualLine> {
    body_lines(body, width)
}

/// A body rendered as markdown, a placeholder for an empty one, the loading note, or why it could not load.
fn body_lines(body: &Body, width: usize) -> Vec<VisualLine> {
    let note = match body {
        Body::Ready(body) if body.trim().is_empty() => labels::EMPTY_BODY.to_owned(),
        Body::Ready(body) => return rendered(body, width),
        Body::Loading => labels::LOADING_BODY.to_owned(),
        Body::Failed(error) => format!("{}: {error}", labels::BODY_FAILED),
    };
    paragraph(&note, width)
}

/// Markdown laid out as a status row's body, left-aligned under the pane's two-column indent.
fn rendered(body: &str, width: usize) -> Vec<VisualLine> {
    let room = u16::try_from(width.saturating_sub(INDENT.len())).unwrap_or(u16::MAX);
    let expanded: Vec<String> = body.lines().map(expand_tabs).collect();
    let mut linked = markdown::status_linked(&expanded.join("\n"), room);
    linked.prefix(|_| Span::raw(INDENT));
    let lines = linked.into_lines();
    let leading = lines
        .iter()
        .take_while(|line| markdown::is_blank_line(line))
        .count();
    lines
        .into_iter()
        .skip(leading)
        .map(|line| {
            let style = line.style;
            let spans = line.spans.into_iter().map(|span| span.patch_style(style));
            VisualLine {
                option: None,
                highlighted: false,
                spans: spans.collect(),
            }
        })
        .collect()
}

/// Dim text wrapped with a two-column indent, one or more lines per line of `content`, tabs expanded to their stops.
pub(super) fn paragraph(content: &str, width: usize) -> Vec<VisualLine> {
    let (_, dim) = list_cursor::styles(false);
    content
        .lines()
        .flat_map(|line| wrap_indented(&format!("  {}", expand_tabs(line)), dim, width))
        .map(|chars| VisualLine {
            option: None,
            highlighted: false,
            spans: merge(&chars),
        })
        .collect()
}

pub(super) fn blank() -> VisualLine {
    VisualLine {
        option: None,
        highlighted: false,
        spans: Vec::new(),
    }
}

#[cfg(test)]
mod tests;

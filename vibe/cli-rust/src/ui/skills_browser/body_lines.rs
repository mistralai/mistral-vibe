//! The body pane's lines: a shared row's description over its body, or a pin's or version's body alone.

use crate::skills_browser::labels;
use crate::skills_browser::model::Body;
use crate::ui::list_cursor;
use crate::ui::visual_lines::{merge, wrap_indented, VisualLine};
use crate::utils::text::expand_tabs;

/// The pane's lines, `width` wide: a shared row's description over the body.
pub(crate) fn wrap(intro: Option<&str>, body: &Body, width: usize) -> Vec<VisualLine> {
    let mut out = Vec::new();
    if let Some(intro) = intro {
        out.extend(paragraph(intro, width));
        out.push(blank());
    }
    out.extend(body_lines(body, width));
    out
}

/// A body's lines, a placeholder for an empty one, the loading note, or why it could not load.
fn body_lines(body: &Body, width: usize) -> Vec<VisualLine> {
    let shown = match body {
        Body::Ready(body) if body.is_empty() => labels::EMPTY_BODY.to_owned(),
        Body::Ready(body) => body.clone(),
        Body::Loading => labels::LOADING_BODY.to_owned(),
        Body::Failed(error) => format!("{}: {error}", labels::BODY_FAILED),
    };
    paragraph(&shown, width)
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

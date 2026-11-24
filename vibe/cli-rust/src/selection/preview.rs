//! The Sessions page preview's selection: the transcript's flow over the painted rows, its chrome, hangs and margins left out.

use ratatui::buffer::Buffer;

use super::flow::{resolve, Flow};
use super::fold;
use super::region::{split, RegionId, RowSpan, ScrollTarget};
use crate::app::App;

/// The highlighted spans, without the pinned prompt and the wrap hangs, as the conversation shows them.
pub(super) fn highlight(app: &App, spans: Vec<RowSpan>) -> Vec<RowSpan> {
    let view = &app.sessions_page.preview.view;
    spans
        .into_iter()
        .flat_map(|span| split(&view.gaps, span))
        .flat_map(|span| split(&view.hangs, span))
        .collect()
}

/// The selected text, read like the conversation's copy: blank rows inside a
/// block kept, the spacing between blocks and the wrap folds dropped.
pub fn copy(app: &App, buf: &Buffer) -> Option<String> {
    let selection = app.selection.region.as_ref()?;
    if selection.owner != RegionId::Preview {
        return None;
    }
    let view = &app.sessions_page.preview.view;
    let region = view.selection;
    let flow = Flow {
        selection: (selection.anchor, selection.head),
        origin: region.origin(ScrollTarget::None),
        end_exclusive: region.end_exclusive,
        chrome: true,
    };
    let area = region.content();
    let granularity = app.selection.granularity;
    let mut spans = resolve(
        flow,
        granularity,
        buf,
        area,
        &view.row_chrome,
        &view.raws,
        true,
    );
    spans.retain(|(y, _, _)| view.margins.binary_search(y).is_err());
    let spans: Vec<RowSpan> = spans
        .into_iter()
        .flat_map(|span| split(&view.gaps, span))
        .collect();
    Some(fold::extract(buf, &spans, &view.folds))
}

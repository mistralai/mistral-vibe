//! Selectable region: resolve a screen drag into selectable cell spans and text.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use crate::app::App;
use crate::selection::flow::{resolve, Flow};
use crate::selection::table;

/// The scrolling document owned by a selectable region.
#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub enum ScrollTarget {
    #[default]
    None,
    Transcript,
    Trust,
}

/// Stable identity for each concurrently painted selectable region. A toast
/// carries its own id, so a selection stays with that toast while the rack
/// shifts it and dies with it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RegionId {
    Main,
    Toast(u64),
    Loading,
    Question,
}

/// The screen region a drag can select, published by whichever surface painted it.
#[derive(Clone, Copy, Default)]
pub struct Region {
    pub area: Rect,
    /// Screen y of document row zero, so a selection survives scrolling.
    pub top: i32,
    /// The region reserved its rightmost column for a scrollbar.
    pub scrollbar: bool,
    /// Stock Textual screens stop before the cell under the pointer; the
    /// transcript keeps it, like Python's `WordSelectScreen`.
    pub end_exclusive: bool,
    /// The region can re-render its document off-screen, so a copy also reaches
    /// rows scrolled out of the viewport. Only the transcript can.
    pub document: bool,
    /// Which document edge-drags scroll, when they begin inside `scroll_area`.
    pub scroll_target: ScrollTarget,
    pub scroll_area: Rect,
}

impl Region {
    /// True when `at` is over selectable text (never the scrollbar gutter).
    pub fn contains(&self, at: (u16, u16)) -> bool {
        at.0 >= self.area.x
            && at.0 < self.area.right()
            && at.1 >= self.area.y
            && at.1 < self.area.bottom()
            && !(self.scrollbar
                && at.0 == self.area.right().saturating_sub(1)
                && (self.scroll_target != ScrollTarget::Trust
                    || (at.1 >= self.scroll_area.y && at.1 < self.scroll_area.bottom())))
    }

    /// Screen cell `at` in coordinates stable for the gesture's owner.
    pub fn point(&self, at: (u16, u16), scroll_target: ScrollTarget) -> (u16, i32) {
        (at.0, at.1 as i32 - self.origin(scroll_target))
    }

    /// The area without its scrollbar gutter, which is chrome.
    pub fn content(&self) -> Rect {
        Rect {
            width: self.area.width.saturating_sub(u16::from(self.scrollbar)),
            ..self.area
        }
    }

    pub fn scroll_target_at(&self, at: (u16, u16)) -> ScrollTarget {
        let area = self.scroll_area;
        if at.0 >= area.x && at.0 < area.right() && at.1 >= area.y && at.1 < area.bottom() {
            self.scroll_target
        } else {
            ScrollTarget::None
        }
    }

    fn origin(&self, scroll_target: ScrollTarget) -> i32 {
        match scroll_target {
            ScrollTarget::None => i32::from(self.area.y),
            ScrollTarget::Transcript | ScrollTarget::Trust => self.top,
        }
    }
}

pub fn get(app: &App, id: RegionId) -> Region {
    match id {
        RegionId::Main => app.view.selection_region,
        RegionId::Toast(_) => app.view.toast_selection_region,
        RegionId::Loading => app.view.loading_selection_region,
        RegionId::Question => app.view.question_selection_region,
    }
}

/// One selected row: inclusive screen columns `[x0, x1]` on screen row `y`.
pub type RowSpan = (u16, u16, u16);

/// Resolve the active selection against the painted frame.
pub fn spans(app: &App, buf: &Buffer, chat: Rect) -> Vec<RowSpan> {
    let Some(selection) = app.selection.region.as_ref() else {
        return Vec::new();
    };
    if let Some(table_cell) = &selection.table_cell {
        return table::screen_spans(app, table_cell, chat);
    }
    let region = get(app, selection.owner);
    let (area, origin) = match selection.scroll_target {
        ScrollTarget::Trust => (region.scroll_area, region.top),
        // The trust footer uses the full width, below the file-list scrollbar.
        ScrollTarget::None if region.scroll_target == ScrollTarget::Trust => {
            (region.area, region.origin(ScrollTarget::None))
        }
        target => (chat, region.origin(target)),
    };
    let chrome = selection.owner == RegionId::Main;
    let gutters: &[RowSpan] = if chrome { &app.view.diff_hitmap } else { &[] };
    let spans = resolve(
        Flow {
            selection: (selection.anchor, selection.head),
            origin,
            end_exclusive: region.end_exclusive,
            chrome,
        },
        app.selection.granularity,
        buf,
        area,
        gutters,
        false,
    );
    if !chrome {
        // The question box cuts its option-prefix cells out of its row spans.
        let gaps = match selection.owner {
            RegionId::Question => &app.view.question_selection_chrome,
            _ => return spans,
        };
        return spans
            .into_iter()
            .flat_map(|span| split(gaps, span))
            .collect();
    }
    spans
        .into_iter()
        .flat_map(|span| split(&app.view.selection_chrome, span))
        .collect()
}

/// Read the complete document selection, including rows outside the viewport.
pub fn extract_document(app: &App) -> Option<String> {
    let selection = app.selection.region.as_ref()?;
    if let Some(table_cell) = &selection.table_cell {
        return Some(table_cell.selected_text(app.selection.granularity));
    }
    let region = get(app, selection.owner);
    if selection.owner == RegionId::Main && app.trust.details.is_some() {
        let document = crate::ui::trust_folders_selection::selection_slice(app)?;
        let spans = resolve(
            Flow {
                selection: (selection.anchor, selection.head),
                origin: 0,
                end_exclusive: region.end_exclusive,
                chrome: false,
            },
            app.selection.granularity,
            &document.buffer,
            document.area,
            &[],
            true,
        );
        return Some(document.extract(&spans));
    }
    // A dialog paints everything it owns, so its copy comes from the frame.
    if !region.document {
        return None;
    }
    let document = crate::ui::transcript::selection_slice(app)?;
    let spans = resolve(
        Flow {
            selection: (selection.anchor, selection.head),
            origin: 0,
            end_exclusive: region.end_exclusive,
            chrome: true,
        },
        app.selection.granularity,
        &document.buffer,
        document.area,
        &document.gutters,
        true,
    );
    Some(extract(&document.buffer, &spans))
}

/// True when a press at `at` anchors a selection: inside the region and on a
/// cell some widget owns, since Textual anchors nothing on bare padding. The
/// chrome map belongs to its region: the transcript's padding and the
/// question box's option prefixes; a toast publishes only its text rows.
pub fn selectable(app: &App, at: (u16, u16), owner: RegionId) -> bool {
    if !get(app, owner).contains(at) {
        return false;
    }
    let chrome = match owner {
        RegionId::Main => &app.view.selection_chrome,
        RegionId::Question => &app.view.question_selection_chrome,
        _ => return true,
    };
    !chrome
        .iter()
        .any(|&(y, x0, x1)| y == at.1 && at.0 >= x0 && at.0 <= x1)
}

/// Cut the cells a screen painted as chrome out of a row span.
fn split(gaps: &[RowSpan], span: RowSpan) -> Vec<RowSpan> {
    let (y, x0, x1) = span;
    let mut spans = vec![span];
    for &(gap_y, gap_x0, gap_x1) in gaps {
        if gap_y != y || gap_x1 < x0 || gap_x0 > x1 {
            continue;
        }
        spans = spans
            .into_iter()
            .flat_map(|(y, x0, x1)| {
                if gap_x1 < x0 || gap_x0 > x1 {
                    return vec![(y, x0, x1)];
                }
                [(y, x0, gap_x0.saturating_sub(1)), (y, gap_x1 + 1, x1)]
                    .into_iter()
                    .filter(|&(_, lo, hi)| lo <= hi && lo >= x0 && hi <= x1)
                    .collect::<Vec<_>>()
            })
            .collect();
    }
    spans
}

/// Read the selected text back from the rendered buffer, one line per row.
pub fn extract(buf: &Buffer, spans: &[RowSpan]) -> String {
    spans
        .iter()
        .map(|&(y, x0, x1)| {
            let row: String = (x0..=x1)
                .filter_map(|x| buf.cell((x, y)).map(|cell| cell.symbol()))
                .collect();
            row.trim_end().to_owned()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

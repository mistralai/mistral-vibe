//! A transcript painted read-only into a box, scrolled up from its end: the Sessions page preview.

use std::collections::HashSet;

use ratatui::layout::Rect;
use ratatui::Frame;

use super::document::{push_rows, Painted};
use super::entry::{self, ExpansionView, QueueView};
use super::layout::{self, DetailsContext};
use super::sticky;
use super::viewport::{document_height, render, Hitmaps, Viewport};
use crate::selection::{Fold, RowChrome, RowSpan};
use crate::transcript::Transcript;
use crate::ui::{markdown, theme};
use crate::utils::transcript_cache::{QueueSpacer, TranscriptCache};

/// What a selection of the painted tail needs, as the conversation's: the
/// rows it rejoins to the row above, each entry's chrome columns, the `/raw`
/// rows, the wrap hangs, the spacing rows between blocks, and the pinned
/// prompt's rows, which no selection takes.
#[derive(Default)]
pub struct Tail {
    pub folds: Vec<(u16, Fold)>,
    pub row_chrome: Vec<RowChrome>,
    pub raws: Vec<(u16, u16)>,
    pub hangs: Vec<RowSpan>,
    pub margins: Vec<u16>,
    pub pinned: Vec<RowSpan>,
    /// The markdown links painted, for clicks and the pointer.
    pub links: Vec<markdown::Link>,
    /// `(top, bottom, header, id)` screen rows of each painted entry, for clicks.
    pub entries: Vec<(u16, u16, Option<u16>, String)>,
    /// The document's height in rows, for keeping a clicked fold in place.
    pub total: u16,
}

/// How the preview shows its transcript: the entries folded open, and the rows
/// scrolled up from the end, clamped to the document as it paints.
pub struct Shown<'a> {
    pub expanded: &'a HashSet<String>,
    pub scroll: &'a mut u16,
    /// The document's height before a fold changed: the scroll, counted from the
    /// end, follows the change so the rows above the end stay where they were.
    pub before: Option<u16>,
    pub collapse: bool,
    /// A turn is running in it: its steps stay unfolded, as in the conversation.
    pub running: bool,
    /// Pin the prompt of the section at the top, as the conversation does (`enable_sticky_prompt`).
    pub sticky: bool,
    /// The pointer, so the link under it paints its hover.
    pub mouse: Option<(u16, u16)>,
}

/// Paint the rows of `transcript` that fit `area`, `shown.scroll` rows up from
/// its end, with settled turns folded like the conversation when `collapse` holds.
pub fn draw_tail(
    f: &mut Frame,
    area: Rect,
    transcript: &Transcript,
    cache: &mut TranscriptCache,
    markdown_cache: &mut markdown::MarkdownCache,
    shown: Shown<'_>,
) -> Tail {
    let Shown {
        expanded,
        scroll,
        before,
        collapse,
        running,
        sticky,
        mouse,
    } = shown;
    if area.is_empty() {
        return Tail::default();
    }
    let width = area.width;
    let details = collapse.then_some(DetailsContext { running });
    cache.ensure_context(width, width, theme::active_index(), false, false);
    cache.ensure_details(details.map(|context| context.running));
    let banner = markdown::LinkedLines::default();
    let built = layout::build(
        cache,
        markdown_cache,
        expanded,
        transcript,
        width,
        (false, false, details),
        u16::MAX,
    );
    let Some(layout) = built else {
        return Tail::default();
    };
    let total = document_height(banner.lines(), layout, width);
    let overflow = total.saturating_sub(area.height);
    if let Some(before) = before {
        *scroll = match total >= before {
            true => scroll.saturating_add(total - before),
            false => scroll.saturating_sub(before - total),
        };
    }
    *scroll = (*scroll).min(overflow);
    let hidden = overflow - *scroll;
    // The entries start one gap row down, as `top` below paints them.
    let pin = sticky::Geometry {
        layout,
        transcript,
        top: 1,
        height: area.height,
        width,
        enabled: sticky,
    };
    let pinned = sticky::measure(&pin, hidden);
    let top = i32::from(area.y) - i32::from(hidden);
    let (mut entries, mut links, mut chrome, mut raws, mut hangs) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let (mut tables, mut rows, mut cat) = (Vec::new(), Vec::new(), None);
    render(
        expanded,
        markdown_cache,
        Hitmaps {
            entries: &mut entries,
            links: &mut links,
            chrome: &mut chrome,
            raws: &mut raws,
            tables: &mut tables,
            rows: &mut rows,
            cat: &mut cat,
            hangs: Some(&mut hangs),
        },
        mouse.filter(|&(x, y)| area.contains((x, y).into())),
        transcript,
        f,
        Viewport {
            layout,
            area,
            width,
            top,
            banner: &banner,
            spacer: QueueSpacer::default(),
            pulse_frame: 0,
            selected: None,
            selected_table: None,
            paused: false,
            queue_hints: &[],
            raw: false,
            rewind: None,
        },
    );
    let covered = sticky::paint(f, area, pinned.as_ref()).map_or(0, |rect| rect.height);
    // The folds of the painted entries, in document rows (the entries start one gap row down).
    let mut folds = Vec::new();
    let mut margins = Vec::new();
    let visible = hidden..hidden + area.height;
    for positioned in layout.entries.iter().filter(|entry| entry.height > 0) {
        let y = positioned.top.saturating_add(1);
        if y.saturating_add(positioned.height) <= hidden || y >= visible.end {
            continue;
        }
        let Some(value) = transcript.entry(positioned.index) else {
            continue;
        };
        let expansion =
            ExpansionView::of(expanded, &value, layout.details_of(positioned.index), false);
        let rendered = entry::render(
            &value,
            width,
            expansion,
            0,
            QueueView::default(),
            false,
            // The paint above filled the cache: this pass reads it back rather
            // than parsing and highlighting every visible entry again.
            Some(&mut *markdown_cache),
        );
        let painted = Painted {
            lines: rendered.lines(),
            folds: rendered.folds(),
            gaps: rendered.gaps(),
        };
        let prewrapped = positioned.prewrapped;
        push_rows(
            &mut folds,
            &mut margins,
            y,
            painted,
            width,
            prewrapped,
            visible.clone(),
        );
    }
    let mut folds: Vec<(u16, Fold)> = folds
        .into_iter()
        .filter(|(y, _)| visible.contains(y))
        .map(|(y, fold)| (area.y + (y - hidden), fold))
        .collect();
    folds.sort_by_key(|(y, _)| *y);
    let margins = margins
        .into_iter()
        .filter(|y| visible.contains(y))
        .map(|y| area.y + (y - hidden))
        .collect();
    // The pinned prompt owns the rows it covers: a click there folds nothing
    // under it, and no selection takes them.
    let first = area.y + covered;
    let pinned = (area.y..first)
        .map(|y| (y, area.x, area.right() - 1))
        .collect();
    let entries = entries
        .into_iter()
        .filter(|(_, bottom, _, _)| *bottom > first)
        .map(|(top, bottom, header, id)| (top.max(first), bottom, header, id))
        .collect();
    Tail {
        folds,
        row_chrome: chrome,
        raws,
        hangs,
        margins,
        pinned,
        // The pinned prompt covers the links painted under it.
        links: links
            .into_iter()
            .filter(|link| !link_covered(link, first))
            .collect(),
        entries,
        total,
    }
}

/// Whether the pinned prompt, above row `first`, hides `link`.
fn link_covered(link: &markdown::Link, first: u16) -> bool {
    link.rows().iter().all(|&(y, _, _)| y < first)
}

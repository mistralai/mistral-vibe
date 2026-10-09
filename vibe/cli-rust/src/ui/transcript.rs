//! Transcript scrollback: renders flattened entries into wrapped lines, top-anchored until it overflows.

mod anchor;
pub(crate) mod diff;
mod difflib;
mod document;
mod entry;
mod layout;
mod paint;
mod table_cells;
mod viewport;

pub use diff::{gutter_width, language, occurrences, render_edit_diff, DiffOccurrence, DiffRow};
pub use difflib::{unified_diff, Hunk, Opcode, Tag};

pub(crate) use document::selection_slice;

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::Frame;

use self::viewport::{document_height, measure, Hitmaps, Viewport};
use super::{markdown, scrollbar, theme};
use crate::app::App;
use crate::selection::{RegionId, ScrollTarget};
use crate::utils::scroll::{absorb_growth, scroll_view};
use crate::utils::transcript_cache::QueueSpacer;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::Transcript);
    let full_w = area.width;
    let content_w = full_w.saturating_sub(1);
    let selected = app.queue.selected.clone();
    let paused = app.queue.paused;
    let rewind = app.rewind.entry_id.clone();
    let selected_table = app
        .selection
        .region
        .as_ref()
        .and_then(|selection| selection.table_cell.as_ref())
        .map(|selection| (selection.key.clone(), selection.head_offset()));
    // The promo is width-wrapped, so each layout width gets its own banner rows.
    let empty = app.view.transcript.is_empty();
    let config = app.view.banner.view(&app.session.startup_config);
    let banner_full = banner(app, config.clone(), full_w, empty);
    let banner_content = banner(app, config, content_w, empty);
    let selecting = app
        .selection
        .region
        .as_ref()
        .is_some_and(|region| region.owner == RegionId::Main);
    let view = &mut app.view;
    view.selection_region.area = area;
    view.selection_region.scroll_target = ScrollTarget::Transcript;
    view.selection_region.scroll_area = area;
    // Only the transcript can re-render rows scrolled out of the viewport.
    view.selection_region.document = true;
    view.transcript_cache
        .ensure_context(full_w, content_w, theme::active_index(), paused);
    view.selection_chrome.clear();
    view.selection_hangs.clear();
    view.entry_hitmap.clear();
    view.link_hitmap.clear();
    view.diff_hitmap.clear();
    view.table_hitmap.clear();
    view.entry_rows.clear();
    let mouse_position = view.mouse_position;
    let pulse_frame = view.pulse_frame;

    // Full width decides overflow, matching the historical measure order.
    let total_full = document_height(
        banner_full.lines(),
        layout::build(
            &mut view.transcript_cache,
            &mut view.markdown_cache,
            &view.expanded,
            &view.transcript,
            full_w,
            paused,
        ),
        full_w,
    );
    if !scroll_view(total_full, area.height, view.scroll).overflow {
        // Short content flows from the top (banner top-left), no scrollbar; the queue stays at the bottom.
        view.scroll_to_entry = None;
        view.scroll_anchor = None;
        view.selection_region.scrollbar = false;
        view.selection_scrollbar.clear();
        view.scroll = 0;
        view.scroll_target = 0;
        view.at_top = true;
        let top = area.y as i32;
        view.selection_region.top = top;
        let layout = view
            .transcript_cache
            .layout(view.transcript.revision(), full_w)
            .expect("full-width layout was cached");
        let spacer = layout.queue_spacer(area.height.saturating_sub(total_full));
        view.queue_spacer = spacer;
        viewport::render(
            &view.expanded,
            &mut view.markdown_cache,
            Hitmaps {
                entries: &mut view.entry_hitmap,
                links: &mut view.link_hitmap,
                diffs: &mut view.diff_hitmap,
                tables: &mut view.table_hitmap,
                rows: &mut view.entry_rows,
                hangs: selecting.then_some(&mut view.selection_hangs),
            },
            mouse_position,
            &view.transcript,
            f,
            Viewport {
                layout,
                area,
                width: full_w,
                top,
                banner: &banner_full,
                spacer,
                pulse_frame,
                selected: selected.as_deref(),
                selected_table: selected_table.as_ref(),
                paused,
                rewind: rewind.as_deref(),
            },
        );
        return;
    }

    // Overflow: reserve a 1-column scrollbar gutter and cull at `width - 1`.
    view.selection_region.scrollbar = true;
    view.queue_spacer = QueueSpacer::default();
    let preparing = view.transcript_cache.preparing_history();
    let content_layout = layout::build(
        &mut view.transcript_cache,
        &mut view.markdown_cache,
        &view.expanded,
        &view.transcript,
        content_w,
        paused,
    );
    let total = document_height(banner_content.lines(), content_layout, content_w);
    let document_top = measure(banner_content.lines(), content_w).saturating_add(1);
    let anchored = view.scroll_anchor.take().and_then(|scroll_anchor| {
        anchor::resolve(
            &scroll_anchor,
            &anchor::Document {
                layout: content_layout,
                transcript: &view.transcript,
                top: document_top,
                total,
                viewport: area.height,
            },
        )
    });
    // Scrolled up: keep the viewport on the same lines as the document grows.
    if !preparing && anchored.is_none() {
        absorb_growth(
            &mut view.scroll,
            &mut view.scroll_target,
            total,
            view.last_total,
        );
    }
    view.last_total = total;
    if let Some((scroll, target)) = anchored {
        view.scroll = scroll;
        view.scroll_target = target;
    }
    // Rewind selection asks for an entry to sit at the top of the viewport; the
    // layout that answers it only exists here (Python `scroll_to_widget`).
    if let Some(index) = view.scroll_to_entry.take() {
        if let Some(entry) = content_layout.entries.iter().find(|e| e.index == index) {
            let document_top = document_top.saturating_add(entry.top);
            let scroll = total
                .saturating_sub(area.height)
                .saturating_sub(document_top);
            view.scroll = scroll;
            view.scroll_target = scroll;
        }
    }
    let scroll = scroll_view(total, area.height, view.scroll);
    view.scroll = scroll.scroll;
    view.at_top = scroll.position == 0;
    view.scroll_target = view.scroll_target.min(total.saturating_sub(area.height));
    let content_area = Rect {
        width: content_w,
        ..area
    };
    let scrollbar_area = Rect {
        x: area.x + content_w,
        width: 1,
        ..area
    };
    view.selection_scrollbar
        .update(scrollbar_area, total, area.height, scroll.position);
    let top = area.y as i32 - scroll.position as i32;
    view.selection_region.top = top;
    viewport::render(
        &view.expanded,
        &mut view.markdown_cache,
        Hitmaps {
            entries: &mut view.entry_hitmap,
            links: &mut view.link_hitmap,
            diffs: &mut view.diff_hitmap,
            tables: &mut view.table_hitmap,
            rows: &mut view.entry_rows,
            hangs: selecting.then_some(&mut view.selection_hangs),
        },
        mouse_position,
        &view.transcript,
        f,
        Viewport {
            layout: content_layout,
            area: content_area,
            width: content_w,
            top,
            banner: &banner_content,
            spacer: QueueSpacer::default(),
            pulse_frame,
            selected: selected.as_deref(),
            selected_table: selected_table.as_ref(),
            paused,
            rewind: rewind.as_deref(),
        },
    );
    scrollbar::draw(
        app,
        f,
        crate::mouse::MouseTarget::Transcript,
        scrollbar_area,
        total,
        area.height,
        scroll.position,
    );
}

/// The rows above the entries: `banner`, the promo with its links, then an empty child view's placeholder.
fn banner(
    app: &App,
    mut banner: markdown::LinkedLines,
    width: u16,
    transcript_empty: bool,
) -> markdown::LinkedLines {
    if let Some(promo) = &app.view.promo {
        banner.append(markdown::guttered(promo, width, theme::ORANGE));
    }
    // Python's `#subagent-transcripts` placeholder (`margin-top: 1`) flows under the banner.
    if let (Some(child_id), true) = (
        app.subagents.viewed_subagent_id.as_deref(),
        transcript_empty,
    ) {
        if let Some(placeholder) = app
            .subagents
            .transcripts
            .child(child_id)
            .and_then(|child| child.placeholder_with(true))
        {
            banner.push_gap();
            banner.push(Line::from(Span::styled(placeholder, theme::muted_style())));
        }
    }
    banner
}

//! Transcript scrollback: renders flattened entries into wrapped lines, bottom-anchored.

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
use ratatui::text::Line;
use ratatui::Frame;

use self::viewport::{document_height, measure, Hitmaps, Viewport};
use super::{markdown, scrollbar, theme};
use crate::app::App;
use crate::selection::ScrollTarget;
use crate::utils::scroll::{absorb_growth, scroll_view};

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::Transcript);
    let full_w = area.width;
    let content_w = full_w.saturating_sub(1);
    let banner_config = app.view.banner.view(&app.session.startup_config);
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
    let banner_full = with_promo(app, banner_config.clone(), full_w);
    let banner_content = with_promo(app, banner_config, content_w);
    let promo_targets = match &app.view.promo {
        Some(promo) => markdown::targets(promo),
        None => Vec::new(),
    };
    let view = &mut app.view;
    view.selection_region.area = area;
    view.selection_region.scroll_target = ScrollTarget::Transcript;
    view.selection_region.scroll_area = area;
    // Only the transcript can re-render rows scrolled out of the viewport.
    view.selection_region.document = true;
    view.transcript_cache
        .ensure_context(full_w, content_w, theme::active_index(), paused);
    view.selection_chrome.clear();
    view.entry_hitmap.clear();
    view.link_hitmap.clear();
    view.diff_hitmap.clear();
    view.table_hitmap.clear();
    let mouse_position = view.mouse_position;
    let pulse_frame = view.pulse_frame;

    // Full width decides overflow, matching the historical measure order.
    let total_full = document_height(
        &banner_full,
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
        // `align-vertical: bottom`: push short content down, no scrollbar.
        view.scroll_to_entry = None;
        view.selection_region.scrollbar = false;
        view.selection_scrollbar.clear();
        view.scroll = 0;
        view.scroll_target = 0;
        let top = area.y as i32 + area.height as i32 - total_full as i32;
        view.selection_region.top = top;
        let layout = view
            .transcript_cache
            .layout(view.transcript.revision(), full_w)
            .expect("full-width layout was cached");
        viewport::render(
            &view.expanded,
            &mut view.markdown_cache,
            Hitmaps {
                entries: &mut view.entry_hitmap,
                links: &mut view.link_hitmap,
                diffs: &mut view.diff_hitmap,
                tables: &mut view.table_hitmap,
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
                promo_targets: &promo_targets,
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
    let content_layout = layout::build(
        &mut view.transcript_cache,
        &mut view.markdown_cache,
        &view.expanded,
        &view.transcript,
        content_w,
        paused,
    );
    let total = document_height(&banner_content, content_layout, content_w);
    // Scrolled up: keep the viewport on the same lines as the document grows.
    absorb_growth(
        &mut view.scroll,
        &mut view.scroll_target,
        total,
        view.last_total,
    );
    view.last_total = total;
    // Rewind selection asks for an entry to sit at the top of the viewport; the
    // layout that answers it only exists here (Python `scroll_to_widget`).
    if let Some(index) = view.scroll_to_entry.take() {
        if let Some(entry) = content_layout.entries.iter().find(|e| e.index == index) {
            let document_top = measure(&banner_content, content_w)
                .saturating_add(1)
                .saturating_add(entry.top);
            let scroll = total
                .saturating_sub(area.height)
                .saturating_sub(document_top);
            view.scroll = scroll;
            view.scroll_target = scroll;
        }
    }
    let scroll = scroll_view(total, area.height, view.scroll);
    view.scroll = scroll.scroll;
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
            promo_targets: &promo_targets,
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

/// The banner rows with the promo mounted under them (Python mounts
/// `VscodeExtensionPromoMessage` before the messages area, directly below it).
fn with_promo(app: &App, mut banner: Vec<Line<'static>>, width: u16) -> Vec<Line<'static>> {
    if let Some(promo) = &app.view.promo {
        banner.extend(markdown::guttered(promo, width, theme::ORANGE));
    }
    banner
}

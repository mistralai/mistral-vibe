//! Mouse text selection: click-chain granularity dispatched to one surface.

pub mod bottom_bar;
pub mod cells;
mod composer;
mod flow;
mod gesture;
pub mod region;
mod table;

pub use bottom_bar::BottomBarSelection;
pub use composer::{in_input, offset_at};
pub use gesture::{ClickChain, Granularity};
pub use region::{Region, RegionId};
pub use table::{TableCellHit, TableCellKey, TableCellSelection, TableCellSpan};

use std::time::Instant;

use crate::app::{App, Selection, Surface};

/// What a release resolved to, so the caller knows whether to run click actions.
pub enum Release {
    /// A selection was made; its copy is in flight and no click action runs.
    Selected,
    /// Press and release on the same region cell: a plain click.
    RegionClick,
    /// Nothing was selected and no surface claims the click.
    Nothing,
}

/// Start a gesture at `at`; the surface under the pointer owns it until release.
pub fn press(app: &mut App, at: (u16, u16)) {
    press_with_padding(app, at, RegionId::Main, false, true);
}

/// Start a gesture in the toast the pointer is over, which owns it until
/// release. A press routed by a rectangle the newest frame no longer paints
/// starts nothing.
pub fn press_toast(app: &mut App, at: (u16, u16)) {
    let Some(id) = crate::ui::toast::claim_region(app, at) else {
        app.selection.region = None;
        cancel_drag(app);
        return;
    };
    press_with_padding(app, at, RegionId::Toast(id), false, false);
}

/// Start a region gesture even when `at` is on its bare centering padding.
pub fn press_including_padding(app: &mut App, at: (u16, u16)) {
    press_with_padding(app, at, RegionId::Main, true, true);
}

fn press_with_padding(
    app: &mut App,
    at: (u16, u16),
    owner: RegionId,
    include_padding: bool,
    include_composer: bool,
) {
    app.selection.bottom_bar = None;
    app.selection.press = Some(at);
    app.selection.dragged = false;
    app.selection.granularity = app.selection.chain.press(at, Instant::now());
    if include_composer {
        if let Some(offset) = offset_at(app, at) {
            app.selection.drag = Some(Surface::Composer);
            app.selection.region = None;
            composer::press(app, offset);
            return;
        }
    }
    let selectable = region::selectable(app, at, owner)
        || (include_padding && region::get(app, owner).contains(at));
    if selectable {
        app.chat_input.anchor = None;
        app.selection.drag = Some(Surface::Region(owner));
        let point = region::get(app, owner).point(at);
        let table_cell = (owner == RegionId::Main)
            .then(|| table::selection_at(app, at))
            .flatten();
        app.selection.region = Some(Selection {
            owner,
            anchor: point,
            head: point,
            pending_copy: false,
            edge_scroll: 0,
            table_cell,
            text: String::new(),
        });
        return;
    }
    app.selection.drag = None;
    app.selection.region = None;
}

/// Extend the in-flight selection to `at`; a gesture never changes owner.
pub fn drag(app: &mut App, at: (u16, u16)) {
    if app.selection.press.is_some_and(|press| press != at) {
        app.selection.dragged = true;
    }
    match app.selection.drag {
        Some(Surface::Composer) => composer::drag(app, at),
        // The overlay clips a cross-boundary screen drag to the region's text.
        Some(Surface::Region(owner)) => {
            let region = region::get(app, owner);
            let head = region.point(at);
            let in_table = app
                .selection
                .region
                .as_ref()
                .is_some_and(|selection| selection.table_cell.is_some());
            // Only the main region scrolls under a drag; the toast has no viewport.
            let edge_scroll = if owner != RegionId::Main {
                0
            } else {
                let edge_row = if in_table {
                    table::drag(app, at)
                } else {
                    Some(i32::from(at.1))
                };
                edge_row.map_or(0, |row| {
                    app.view.transcript_scrollbar.selection_edge_scroll(row)
                })
            };
            if let Some(selection) = app.selection.region.as_mut() {
                selection.head = head;
                selection.edge_scroll = edge_scroll;
            }
        }
        // The bottom bar routes its own drags through `bottom_bar::drag`.
        Some(Surface::BottomBar) | None => {}
    }
}

/// End the gesture and report what it resolved to.
pub fn release(app: &mut App) -> Release {
    stop_auto_scroll(app);
    let surface = app.selection.drag.take();
    let dragged = app.selection.dragged;
    app.selection.press = None;
    app.selection.dragged = false;
    // Python only sets `_dragged` from the word/paragraph expansion path, so a
    // plain char drag leaves the chain alive and the next press still chains.
    app.selection
        .chain
        .release(dragged && app.selection.granularity != Granularity::Char);
    match surface {
        Some(Surface::Composer) => {
            match composer::release(app) {
                // Python `on_mouse_up`: no autocopy, no copy and no notice.
                Some(text) if app.session.startup_config.autocopy_to_clipboard => {
                    crate::ui::notice::copied(app, &text);
                }
                _ => {}
            }
            Release::Selected
        }
        // The text is only known once the frame is painted, so the copy is
        // deferred to the overlay, which clears an empty selection itself.
        Some(Surface::Region(_)) => match app.selection.region.as_mut() {
            Some(selection)
                if selection.anchor != selection.head
                    || app.selection.granularity != Granularity::Char =>
            {
                selection.pending_copy = true;
                Release::Selected
            }
            _ => {
                app.selection.region = None;
                if dragged {
                    Release::Nothing
                } else {
                    Release::RegionClick
                }
            }
        },
        // The bottom bar owns a single-row column range, but rides the shared
        // click chain and release above so multi-click granularity carries over.
        Some(Surface::BottomBar) => bottom_bar::release(app),
        None => {
            app.selection.region = None;
            Release::Nothing
        }
    }
}

/// A scroll drops the composer selection; a transcript one rides the document.
pub fn scrolled(app: &mut App) {
    app.chat_input.anchor = None;
    app.selection.composer_anchor = None;
    if app.selection.drag == Some(Surface::Composer) {
        app.selection.drag = None;
    }
}

pub fn is_auto_scrolling(app: &App) -> bool {
    app.selection
        .region
        .as_ref()
        .is_some_and(|selection| selection.edge_scroll != 0)
}

pub fn auto_scroll(app: &mut App) -> bool {
    let Some((stored_edge_scroll, in_table)) = app
        .selection
        .region
        .as_ref()
        .map(|selection| (selection.edge_scroll, selection.table_cell.is_some()))
    else {
        return false;
    };
    let edge_scroll = if in_table {
        table::head_screen_row(app).map_or(0, |row| {
            app.view.transcript_scrollbar.selection_edge_scroll(row)
        })
    } else {
        stored_edge_scroll
    };
    if edge_scroll == 0 {
        stop_auto_scroll(app);
        return false;
    }
    let Some(max_scroll) = app.view.transcript_scrollbar.max_scroll() else {
        stop_auto_scroll(app);
        return false;
    };
    let old_scroll = app.view.scroll;
    let amount = u16::from(edge_scroll.unsigned_abs());
    let scroll = if edge_scroll < 0 {
        old_scroll.saturating_add(amount).min(max_scroll)
    } else {
        old_scroll.saturating_sub(amount)
    };
    let delta = i32::from(scroll) - i32::from(old_scroll);
    if delta == 0 {
        stop_auto_scroll(app);
        return false;
    }
    app.view.scroll = scroll;
    app.view.scroll_target = scroll;
    if let Some(selection) = app.selection.region.as_mut() {
        selection.head.1 -= delta;
    }
    if in_table {
        table::follow_scroll(app);
    }
    true
}

pub fn stop_auto_scroll(app: &mut App) {
    if let Some(selection) = app.selection.region.as_mut() {
        selection.edge_scroll = 0;
    }
}

pub fn cancel_drag(app: &mut App) {
    app.selection.drag = None;
    app.selection.press = None;
    app.selection.dragged = false;
    app.selection.composer_anchor = None;
    app.selection.bottom_bar = None;
    stop_auto_scroll(app);
}

/// Clear a selection when its rendered region disappears or changes content.
pub fn clear_region(app: &mut App, owner: RegionId) {
    if app
        .selection
        .region
        .as_ref()
        .is_some_and(|selection| selection.owner == owner)
    {
        app.selection.region = None;
    }
    if app.selection.drag == Some(Surface::Region(owner)) {
        cancel_drag(app);
    }
}

//! Hovering a transcript link to a local file previews its code in a popup.

pub mod load;
pub mod target;
pub mod telemetry;

use std::time::Duration;

use ratatui::layout::Rect;

use crate::app::{App, Surface};
use crate::clock::UiInstant;
use crate::focus::Focus;
use crate::mouse::MouseTarget;
use crate::selection::{Region, RegionId, RowSpan};
use crate::ui::markdown::LinkKind;

pub use load::Snippet;
use telemetry::Close;

/// How long the pointer rests on a link before its preview opens.
pub const DELAY: Duration = Duration::from_millis(300);
/// Lines shown above the focused line when the preview opens.
const CONTEXT: usize = 3;

#[derive(Default)]
pub struct CodePreview {
    /// The hovered link's target and the cell the pointer entered it at.
    pub link: Option<(String, (u16, u16))>,
    pub show_at: Option<UiInstant>,
    pub shown: bool,
    pub snippet: Option<Snippet>,
    /// Index of the first snippet line shown, clamped at paint.
    pub scroll: usize,
    /// The last scroll the last frame could show.
    pub max_scroll: usize,
    /// The popup painted by the last frame.
    pub area: Rect,
    /// The code text a drag can select, without the line numbers.
    pub selection: Region,
    /// The line-number gutter cells, cut out of a selection.
    pub gutters: Vec<RowSpan>,
    pub visit: Option<telemetry::Visit>,
}

/// A snippet read for the link `target`.
pub struct Event {
    target: String,
    snippet: Result<Snippet, load::Failure>,
}

/// Follow the pointer: a new file link arms the preview, leaving the link and popup clears it.
pub fn hover(app: &mut App) {
    let Some(at) = app.view.mouse_position.filter(|_| transcript_focused(app)) else {
        let close = match transcript_focused(app) {
            true => Close::PointerLeft,
            false => Close::FocusChanged,
        };
        return clear(app, close);
    };
    if contains(app.code_preview.area, at) || selecting(app) {
        return;
    }
    let on_transcript = crate::mouse::target_at(app, at) == Some(MouseTarget::Transcript);
    let link =
        app.view.link_hitmap.iter().find(|link| {
            on_transcript && link.kind() != LinkKind::InlineImage && link.contains(at)
        });
    let Some(link) = link else {
        return clear(app, Close::PointerLeft);
    };
    let same =
        |(target, cell): &(String, (u16, u16))| target == link.target() && link.contains(*cell);
    if app.code_preview.link.as_ref().is_some_and(same) {
        return;
    }
    let target = link.target().to_owned();
    if target::parse(&target, app.session.cwd.as_deref()).is_none() {
        return clear(app, Close::PointerLeft);
    }
    telemetry::closed(app, Close::LinkChanged);
    app.code_preview = CodePreview {
        link: Some((target, at)),
        show_at: Some(crate::clock::now() + DELAY),
        ..CodePreview::default()
    };
}

/// The pointer rested long enough: open the armed preview and load its file.
pub fn show(app: &mut App) {
    app.code_preview.show_at = None;
    let Some((target, _)) = app.code_preview.link.clone() else {
        return;
    };
    app.code_preview.shown = true;
    let cwd = app.session.cwd.clone();
    let Some(location) = target::parse(&target, cwd.as_deref()) else {
        return;
    };
    telemetry::started(app, &location);
    crate::ui_event::spawn(app, async move {
        let snippet = crate::work::spawn_blocking(move || load::read(&location, cwd.as_deref()))
            .await
            .unwrap_or(Err(load::Failure::Unreadable));
        Event { target, snippet }
    });
}

pub fn apply_event(app: &mut App, event: Event) {
    let preview = &mut app.code_preview;
    if !matches!(&preview.link, Some((target, _)) if *target == event.target) {
        return;
    }
    preview.scroll = event
        .snippet
        .as_ref()
        .ok()
        .and_then(|snippet| snippet.row_of(snippet.focus?.0))
        .map_or(0, |row| row.saturating_sub(CONTEXT));
    let failure = event.snippet.as_ref().err().copied();
    preview.snippet = event.snippet.ok();
    if let Some(failure) = failure {
        telemetry::failed(app, failure);
    }
}

/// A press outside the popup closes it, so it never covers a selection dragged from the link.
pub fn press(app: &mut App, at: (u16, u16)) {
    if !contains(app.code_preview.area, at) {
        clear(app, Close::ClickOutside);
    }
}

/// A key press closes the preview until the pointer enters another link; true when it was open.
pub fn dismiss(app: &mut App) -> bool {
    let preview = &mut app.code_preview;
    preview.show_at = None;
    preview.shown = false;
    !std::mem::take(&mut preview.area).is_empty()
}

/// Scroll by a wheel step; at an edge nothing moves, so the selection stays.
pub fn wheel(app: &mut App, up: bool) {
    let step = crate::mouse::MOUSE_SCROLL_STEP as usize;
    let preview = &app.code_preview;
    let scroll = match up {
        true => preview.scroll.saturating_sub(step),
        false => preview.scroll.saturating_add(step).min(preview.max_scroll),
    };
    if scroll != preview.scroll {
        scroll_to(app, scroll);
    }
}

/// Scroll the snippet; a selection is anchored to the screen, so it goes.
pub fn scroll_to(app: &mut App, scroll: usize) {
    crate::selection::clear_region(app, RegionId::CodePreview);
    let preview = &mut app.code_preview;
    if let Some(visit) = preview.visit.as_mut() {
        visit.has_scrolled |= scroll != preview.scroll;
    }
    preview.scroll = scroll;
}

/// Whether no bottom app or modal has keyboard focus, so the preview may draw over the transcript.
pub fn transcript_focused(app: &App) -> bool {
    matches!(app.focus(), Focus::Composer | Focus::SubagentList)
}

/// Close the preview, recording why.
pub fn clear(app: &mut App, close: Close) {
    telemetry::closed(app, close);
    crate::selection::clear_region(app, RegionId::CodePreview);
    app.code_preview = CodePreview::default();
}

/// A drag or an uncopied selection in the popup keeps it open until the next frame copies it.
fn selecting(app: &App) -> bool {
    let owner = RegionId::CodePreview;
    app.selection.drag == Some(Surface::Region(owner))
        || app
            .selection
            .region
            .as_ref()
            .is_some_and(|selection| selection.owner == owner && selection.pending_copy)
}

fn contains(area: Rect, at: (u16, u16)) -> bool {
    area.contains(at.into())
}

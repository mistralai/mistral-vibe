//! Capture, drag, and track-page any registered vertical scrollbar.

use ratatui::layout::Rect;

use super::{contains, MouseRegion, MouseTarget};
use crate::app::App;
use crate::selection;
use crate::ui::scrollbar::{Side, State};

/// Auto-scroll ticks a held track click waits before repeating.
const TRACK_REPEAT_DELAY: u8 = 6;

#[derive(Clone, Copy)]
struct Drag {
    target: MouseTarget,
    state: State,
    max: usize,
    /// The latest drag position; `None` until the first drag event, so a
    /// drag begin does not jump its owner to the top.
    position: Option<usize>,
}

#[derive(Clone, Copy)]
struct Track {
    target: MouseTarget,
    state: State,
    at: (u16, u16),
    side: Side,
    delay: u8,
}

#[derive(Default)]
pub(super) struct Scrollbars {
    drag: Option<Drag>,
    track: Option<Track>,
}

pub fn register_scrollbar(
    app: &mut App,
    target: MouseTarget,
    area: Rect,
    virtual_size: usize,
    window_size: usize,
    position: usize,
) {
    let mut state = State::default();
    state.update_large(area, virtual_size, window_size, position);
    if let Some(region) = app
        .view
        .mouse_regions
        .iter_mut()
        .rev()
        .find(|region| region.target == target)
    {
        region.scrollbar = Some(state);
    } else {
        app.view.mouse_regions.push(MouseRegion {
            area,
            target,
            scrollbar: Some(state),
        });
    }
}

/// The tracks of the scrollbars painted so far this frame that overlap `within`.
pub fn scrollbar_tracks(app: &App, within: Rect) -> Vec<Rect> {
    app.view
        .mouse_regions
        .iter()
        .filter_map(|region| region.scrollbar.and_then(|state| state.area()))
        .filter(|track| track.intersects(within))
        .collect()
}

pub(super) fn contains_track(app: &App, at: (u16, u16)) -> bool {
    app.view
        .mouse_regions
        .iter()
        .rev()
        .find(|region| contains(region.area, at))
        .and_then(|region| region.scrollbar)
        .is_some_and(|state| state.contains(at))
}

pub(super) fn begin(app: &mut App, at: (u16, u16)) -> bool {
    let Some((target, mut state)) = scrollbar_at(app, at) else {
        cancel(app);
        return false;
    };
    if !state.begin_drag(at) {
        cancel(app);
        return false;
    }
    let Some(max) = state.max_scroll_large() else {
        return false;
    };
    app.view.mouse.scrollbars.drag = Some(Drag {
        target,
        state,
        max,
        position: None,
    });
    press(app);
    true
}

/// Page toward a track click outside the thumb (Textual `ScrollUp`/`ScrollDown`) and hold it.
pub(super) fn begin_track(app: &mut App, at: (u16, u16)) -> bool {
    let Some((target, state)) = scrollbar_at(app, at) else {
        return false;
    };
    let Some(side) = state.track_side(at) else {
        return false;
    };
    press(app);
    let mut track = Track {
        target,
        state,
        at,
        side,
        delay: TRACK_REPEAT_DELAY,
    };
    page(app, &mut track);
    app.view.mouse.scrollbars.track = Some(track);
    true
}

pub(super) fn move_track(app: &mut App, at: (u16, u16)) {
    if let Some(track) = app.view.mouse.scrollbars.track.as_mut() {
        track.at = at;
    }
}

/// A held track click still has pages to scroll before the thumb reaches the pointer.
pub(super) fn is_track_paging(app: &App) -> bool {
    live_track(app).is_some_and(|track| track.state.track_side(track.at) == Some(track.side))
}

pub(super) fn repeat_track(app: &mut App) {
    let Some(mut track) = live_track(app) else {
        app.view.mouse.scrollbars.track = None;
        return;
    };
    if track.state.track_side(track.at) != Some(track.side) {
        return;
    }
    if track.delay == 0 {
        page(app, &mut track);
    } else {
        track.delay -= 1;
    }
    app.view.mouse.scrollbars.track = Some(track);
}

/// The held track against the latest painted geometry, keeping its own position across content growth.
fn live_track(app: &App) -> Option<Track> {
    let mut track = app.view.mouse.scrollbars.track?;
    let live = app
        .view
        .mouse_regions
        .iter()
        .rev()
        .find(|region| region.target == track.target)?
        .scrollbar?;
    track.state.follow(live);
    Some(track)
}

fn page(app: &mut App, track: &mut Track) {
    let (Some(position), Some(max)) =
        (track.state.page(track.side), track.state.max_scroll_large())
    else {
        return;
    };
    if track.target == MouseTarget::Transcript {
        app.view.scroll_target = transcript_scroll(position, max);
        return;
    }
    apply_position(app, track.target, position, max);
}

pub(super) fn drag(app: &mut App, row: u16) -> bool {
    let Some(mut drag) = app.view.mouse.scrollbars.drag else {
        return false;
    };
    let Some(position) = drag.state.drag_to(row) else {
        return false;
    };
    drag.position = Some(position);
    app.view.mouse.scrollbars.drag = Some(drag);
    apply_position(app, drag.target, position, drag.max);
    true
}

/// The live position of a scrollbar drag for `target`, if one is in flight.
/// Owners whose scroll state is not app-owned (the setup wizard's theme
/// preview) read it while the drag runs and copy it into their own state.
pub fn drag_scroll(app: &App, target: MouseTarget) -> Option<usize> {
    let drag = app.view.mouse.scrollbars.drag?;
    (drag.target == target).then_some(drag.position).flatten()
}

pub(super) fn cancel(app: &mut App) {
    app.view.mouse.scrollbars.drag = None;
    app.view.mouse.scrollbars.track = None;
}

fn scrollbar_at(app: &App, at: (u16, u16)) -> Option<(MouseTarget, State)> {
    app.view
        .mouse_regions
        .iter()
        .rev()
        .find(|region| contains(region.area, at))
        .and_then(|region| region.scrollbar.map(|state| (region.target, state)))
}

fn press(app: &mut App) {
    app.overlays.notice = None;
    selection::cancel_drag(app);
    app.selection.region = None;
    app.chat_input.anchor = None;
}

fn transcript_scroll(position: usize, max: usize) -> u16 {
    u16::try_from(max - position.min(max)).unwrap_or(u16::MAX)
}

fn apply_position(app: &mut App, target: MouseTarget, position: usize, max: usize) {
    let position = position.min(max);
    match target {
        MouseTarget::Transcript => {
            let scroll = transcript_scroll(position, max);
            app.view.scroll = scroll;
            app.view.scroll_target = scroll;
        }
        MouseTarget::Completion => {
            app.completion.scroll = position;
            app.completion.reveal = false;
        }
        MouseTarget::ThemePicker => {
            app.theme_picker.scroll = position;
            app.theme_picker.free_scroll = true;
        }
        MouseTarget::ModelPicker => {
            app.model_picker.scroll = position;
            app.model_picker.free_scroll = true;
        }
        MouseTarget::ResumePicker => {
            app.resume_picker.scroll = position;
            app.resume_picker.free_scroll = true;
        }
        MouseTarget::RemoteProject => {
            app.vibe_code_project.scroll = position;
            app.vibe_code_project.free_scroll = true;
        }
        MouseTarget::Mcp => {
            app.mcp.scroll = position;
            app.mcp.free_scroll = true;
        }
        MouseTarget::Plugins => {
            app.plugins.scroll = position;
            app.plugins.free_scroll = true;
        }
        MouseTarget::Config => {
            app.config_screen.scroll = position;
            app.config_screen.free_scroll = true;
        }
        MouseTarget::ConfigEditor => {
            if let Some(edit) = app.config_screen.edit.as_mut() {
                edit.scroll = Some(position);
            }
        }
        MouseTarget::Approval => app.approval.detail_scroll = position,
        MouseTarget::Question => app
            .question_app
            .viewport
            .detach_at(u16::try_from(position).unwrap_or(u16::MAX)),
        MouseTarget::Trust => app.trust.scroll = position,
        MouseTarget::ProxySetup => {
            app.proxy_setup.scroll = position;
            app.proxy_setup.free_scroll = true;
        }
        // The wizard's theme preview reads the live drag position itself
        // (`drag_scroll`): its scroll state is not app-owned.
        MouseTarget::Blocked
        | MouseTarget::Toast
        | MouseTarget::Composer
        | MouseTarget::Loading
        | MouseTarget::LogLevelPicker
        | MouseTarget::BottomApp
        | MouseTarget::McpOAuth
        | MouseTarget::ConnectorAuth
        | MouseTarget::BottomBar
        | MouseTarget::TodoRow
        | MouseTarget::TodoSidebar
        | MouseTarget::SubagentList
        | MouseTarget::OnboardingPreview
        | MouseTarget::OnboardingThemeList
        | MouseTarget::OnboardingLinks
        | MouseTarget::OnboardingInputs => {}
    }
}

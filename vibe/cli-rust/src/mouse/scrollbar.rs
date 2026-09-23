//! Capture and drag any registered vertical scrollbar.

use ratatui::layout::Rect;

use super::{contains, MouseRegion, MouseTarget};
use crate::app::App;
use crate::selection;
use crate::ui::scrollbar::State;

#[derive(Clone, Copy)]
struct Drag {
    target: MouseTarget,
    state: State,
    max: usize,
}

#[derive(Default)]
pub(super) struct Scrollbars {
    drag: Option<Drag>,
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
    let candidate = app
        .view
        .mouse_regions
        .iter()
        .rev()
        .find(|region| contains(region.area, at))
        .and_then(|region| region.scrollbar.map(|state| (region.target, state)));
    let Some((target, mut state)) = candidate else {
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
    app.view.mouse.scrollbars.drag = Some(Drag { target, state, max });
    app.overlays.notice = None;
    selection::cancel_drag(app);
    app.selection.region = None;
    app.chat_input.anchor = None;
    true
}

pub(super) fn drag(app: &mut App, row: u16) -> bool {
    let Some(drag) = app.view.mouse.scrollbars.drag else {
        return false;
    };
    let Some(position) = drag.state.drag_to(row) else {
        return false;
    };
    apply_position(app, drag.target, position, drag.max);
    true
}

pub(super) fn end(app: &mut App) {
    app.view.mouse.scrollbars.drag = None;
}

pub(super) fn cancel(app: &mut App) {
    app.view.mouse.scrollbars.drag = None;
}

fn apply_position(app: &mut App, target: MouseTarget, position: usize, max: usize) {
    let position = position.min(max);
    match target {
        MouseTarget::Transcript => {
            let scroll = u16::try_from(max - position).unwrap_or(u16::MAX);
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
        MouseTarget::Blocked
        | MouseTarget::Toast
        | MouseTarget::Composer
        | MouseTarget::Loading
        | MouseTarget::LogLevelPicker
        | MouseTarget::Rewind
        | MouseTarget::McpOAuth
        | MouseTarget::ConnectorAuth
        | MouseTarget::BottomBar
        | MouseTarget::TodoRow
        | MouseTarget::TodoSidebar => {}
    }
}

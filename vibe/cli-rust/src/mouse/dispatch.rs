//! Target-specific mouse behavior after routing and capture.

use std::sync::Arc;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use tokio::sync::mpsc;

use super::MouseTarget;
use crate::app::App;
use crate::config;
use crate::selection::{self, Release};
use crate::server::Client;
use crate::utils::scroll::ScrollAnchor;
use crate::{approval, connector_auth, external_url, mcp, mcp_oauth, question_input};

pub(super) fn dispatch(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    target: MouseTarget,
    event: MouseEvent,
) {
    let at = (event.column, event.row);
    match target {
        MouseTarget::Transcript | MouseTarget::Composer => selection_event(app, target, event),
        MouseTarget::Toast => toast_event(app, event),
        MouseTarget::BottomBar => bottom_bar_event(app, event),
        MouseTarget::Loading => loading_event(app, event),
        MouseTarget::Approval => approval::handle_mouse(app, event),
        MouseTarget::Question => question_input::handle_mouse(app, event),
        MouseTarget::RemoteProject => crate::vibe_code_project::input::mouse(app, client, event),
        MouseTarget::Mcp => match event.kind {
            MouseEventKind::Down(MouseButton::Left) => mcp::press(app, at),
            MouseEventKind::Up(MouseButton::Left) => mcp::release(app, at),
            _ => {}
        },
        MouseTarget::McpOAuth => match event.kind {
            MouseEventKind::Down(MouseButton::Left) => mcp_oauth::press(app, at),
            MouseEventKind::Up(MouseButton::Left) => mcp_oauth::release(app, at),
            _ => {}
        },
        MouseTarget::ConnectorAuth => match event.kind {
            MouseEventKind::Down(MouseButton::Left) => connector_auth::press(app, at),
            MouseEventKind::Up(MouseButton::Left) => connector_auth::release(app, at),
            _ => {}
        },
        MouseTarget::Trust => crate::trust_folders::handle_mouse(app, event),
        // The pinned todo line opens the full plan, like Python's row click.
        MouseTarget::TodoRow => {
            if matches!(event.kind, MouseEventKind::Down(MouseButton::Left)) {
                app.todo_sidebar.open = true;
            }
        }
        MouseTarget::TodoSidebar => {}
        MouseTarget::SubagentList => subagent_list_event(app, event),
        MouseTarget::Config | MouseTarget::ConfigEditor => {
            config::handle_mouse(app, client, config_tx, event)
        }
        // The wizard's regions never reach the loop (the wizard runs
        // pre-session and routes its own mouse events).
        MouseTarget::Blocked
        | MouseTarget::Completion
        | MouseTarget::ThemePicker
        | MouseTarget::ModelPicker
        | MouseTarget::LogLevelPicker
        | MouseTarget::ResumePicker
        | MouseTarget::Rewind
        | MouseTarget::OnboardingThemeList
        | MouseTarget::OnboardingPreview
        | MouseTarget::OnboardingLinks
        | MouseTarget::OnboardingInputs => {}
    }
}

/// The subagent list: hover moves the marker, a click highlights then selects
/// (Python `on_mouse_move` + OptionList click).
fn subagent_list_event(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    let Some(index) = app
        .subagents
        .list
        .row_areas
        .iter()
        .find(|(area, _)| {
            at.0 >= area.x && at.0 < area.right() && at.1 >= area.y && at.1 < area.bottom()
        })
        .map(|(_, index)| *index)
    else {
        if event.kind == MouseEventKind::Moved {
            app.subagents.list.mouse_session_id = None;
        }
        return;
    };
    let session_id = if index == 0 {
        None
    } else {
        app.subagents
            .list
            .rows
            .get(index - 1)
            .map(|row| row.id.clone())
    };
    match event.kind {
        MouseEventKind::Moved => {
            app.subagents.list.mouse_session_id =
                Some(session_id.unwrap_or_else(|| crate::subagents::MAIN_SESSION_ID.to_owned()));
        }
        MouseEventKind::Down(MouseButton::Left) => {
            app.subagents.list.mouse_session_id = None;
            app.subagents.list.highlighted = index;
            app.subagents.list.focused = true;
            app.subagents.list.press_row = Some(index);
            app.set_app_focus(false);
        }
        // A click is press then release on the same option (Textual OptionList).
        MouseEventKind::Up(MouseButton::Left) => {
            let pressed = app.subagents.list.press_row.take();
            if pressed == Some(index) {
                crate::subagents::select(app, session_id);
            }
        }
        _ => {}
    }
}

/// Selecting an owned region copies its text; it has no click action.
fn owned_region_event(app: &mut App, event: MouseEvent, press: fn(&mut App, (u16, u16))) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => press(app, at),
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            selection::release(app);
        }
        _ => {}
    }
}

/// Selecting the loading row copies its text; the row owns no click action.
fn loading_event(app: &mut App, event: MouseEvent) {
    owned_region_event(app, event, |app, at| {
        selection::press_owned(app, at, selection::RegionId::Loading)
    });
}

/// Selecting toast text never dismisses the toast, which self-hides on timeout.
fn toast_event(app: &mut App, event: MouseEvent) {
    owned_region_event(app, event, selection::press_toast);
}

fn bottom_bar_event(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            // Honor a copy armed by the previous release before this press drops
            // it; the text was cached at paint time, so no buffer is read.
            crate::ui::bottom_bar::honor_pending_copy(app);
            app.overlays.notice = None;
            selection::bottom_bar::press(app, at);
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::bottom_bar::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            selection::release(app);
        }
        _ => {}
    }
}

fn selection_event(app: &mut App, target: MouseTarget, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            // Python keeps the input box enabled while the list is focused, so
            // clicking the composer hands it focus (Textual widget click).
            if target == MouseTarget::Composer && app.subagents.list.focused {
                crate::subagents::focus_input(app);
            }
            // Python keeps the inline notice across a transcript mouse-down
            // (it clears only via its own timeout or a queue-mode event).
            app.selection.had_selection_at_press =
                app.selection.region.is_some() || app.selection.bottom_bar.is_some();
            selection::press(app, at);
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            let released = selection::release(app);
            if target != MouseTarget::Transcript || matches!(released, Release::Selected) {
                return;
            }
            if let Some(link) = app.view.link_hitmap.iter().find(|link| link.contains(at)) {
                match link.kind() {
                    crate::ui::markdown::LinkKind::External => external_url::open(link.target()),
                    crate::ui::markdown::LinkKind::Attachment => {
                        external_url::open_file(link.target())
                    }
                    crate::ui::markdown::LinkKind::InlineImage => {
                        crate::inline_images::open(app, link.target())
                    }
                }
            } else if matches!(released, Release::RegionClick) {
                toggle_effect_at(app, event.row);
            }
        }
        _ => {}
    }
}

/// Toggle the entry or group a transcript row hit, following the viewed
/// child's transcript and cache when a sub-agent view is open.
pub fn toggle_effect_at(app: &mut App, row: u16) {
    let Some((header, id)) = app
        .view
        .entry_hitmap
        .iter()
        .find(|(top, bottom, _, _)| row >= *top && row < *bottom)
        .map(|(_, _, header, id)| (*header, id.clone()))
    else {
        return;
    };
    // Python `_click_is_passive`: with a selection showing, only the header row toggles.
    if app.selection.had_selection_at_press && header != Some(row) {
        return;
    }
    // The child view swaps its transcript and cache in only at render time,
    // so the expandability check and the layout cache to invalidate both
    // follow the viewed child, like `App::toggle_tools`.
    if let Some(child) = app
        .subagents
        .viewed_subagent_id
        .as_deref()
        .and_then(|child_id| app.subagents.transcripts.child_mut(child_id))
    {
        if !child.transcript.is_expandable(&id) {
            return;
        }
        child.cache.invalidate_layouts();
    } else {
        if !app.view.transcript.is_expandable(&id) {
            return;
        }
        app.view.transcript_cache.invalidate_layouts();
    }
    let reveal = !app.view.expanded.remove(&id);
    if reveal {
        app.view.expanded.insert(id.clone());
    }
    anchor_toggle(app, row, id, reveal);
}

/// Hold the clicked entry steady; an expansion then scrolls just enough to show what it revealed.
fn anchor_toggle(app: &mut App, row: u16, key: String, reveal: bool) {
    let landing = row.saturating_sub(app.view.selection_region.area.y);
    let clicked = i32::from(landing);
    app.view.scroll_anchor = app
        .view
        .entry_rows
        .iter()
        .find(|(_, top, height)| (*top..*top + i32::from(*height)).contains(&clicked))
        .map(|&(index, row, height)| ScrollAnchor {
            index,
            row,
            height,
            landing,
            key: Some(key),
            reveal,
        });
}

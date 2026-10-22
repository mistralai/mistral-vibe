//! Target-specific mouse behavior after routing and capture.

use std::sync::Arc;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use tokio::sync::mpsc;

use super::{option_gesture, MouseTarget};
use crate::app::App;
use crate::config;
use crate::config_write::Scope;
use crate::option_list::{Click, MultiAction, OptionList, OptionView, SingleAction};
use crate::selection::{self, RegionId, Release};
use crate::server::Client;
use crate::transcript::grouping::KEY_PREFIX;
use crate::utils::scroll::ScrollAnchor;
use crate::{
    approval, connector_auth, external_url, mcp, mcp_oauth, model_picker, question_input,
    resume_picker, thinking_picker,
};

pub(super) fn dispatch(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    target: MouseTarget,
    event: MouseEvent,
) {
    match target {
        MouseTarget::Transcript | MouseTarget::Composer => selection_event(app, target, event),
        MouseTarget::Toast => toast_event(app, event),
        MouseTarget::BottomBar => bottom_bar_event(app, event),
        MouseTarget::Loading => loading_event(app, event),
        MouseTarget::Question => question_input::handle_mouse(app, client, event),
        MouseTarget::RemoteProject => crate::vibe_code_project::input::mouse(app, client, event),
        MouseTarget::Approval => approval::handle_mouse(app, client, event),
        MouseTarget::Mcp => mcp::mouse(app, event),
        MouseTarget::Plugins => crate::plugins::mouse(app, event),
        MouseTarget::McpOAuth => {
            if picked(app, event, |app| &mut app.mcp_oauth.list) {
                mcp_oauth::select(app);
            }
        }
        MouseTarget::ConnectorAuth => {
            if picked(app, event, |app| &mut app.connector_auth.list) {
                connector_auth::select(app);
            }
        }
        MouseTarget::ModelPicker => {
            if confirmed(app, event, |app| &mut app.model_picker.list) {
                model_picker::select(app, client, Scope::Saved);
            }
        }
        MouseTarget::ThemePicker => {
            if picked(app, event, |app| &mut app.theme_picker.list) {
                crate::input::select_theme(app, client);
            }
        }
        MouseTarget::ThinkingPicker => {
            if confirmed(app, event, |app| &mut app.thinking_picker.list) {
                let scope = thinking_picker::enter_scope(app);
                thinking_picker::select(app, client, scope);
            }
        }
        MouseTarget::ResumePicker => {
            let click = option_gesture(app, event, RegionId::BottomApp, |app| {
                &mut app.resume_picker.list
            });
            if let Some(click) = click {
                resume_picker::click(app, client, click);
            }
        }
        MouseTarget::ProxySetup => bottom_app_event(app, event, |app, event| {
            if event.kind == MouseEventKind::Down(MouseButton::Left) {
                crate::proxy_setup::press(app, (event.column, event.row));
            }
        }),
        MouseTarget::LogLevelPicker => {
            if picked(app, event, |app| &mut app.log_level_picker.list) {
                crate::log_level_picker::click(app, (event.column, event.row));
            }
        }
        MouseTarget::BottomApp => bottom_app_event(app, event, |_, _| {}),
        MouseTarget::Trust => crate::trust_folders::handle_mouse(app, event),
        // A click on the pinned todo line opens the full plan.
        MouseTarget::TodoRow => {
            if matches!(event.kind, MouseEventKind::Down(MouseButton::Left)) {
                app.todo_sidebar.open = true;
            }
        }
        MouseTarget::TodoSidebar => {}
        // The console log body: the wheel scrolls it (loading older pages at
        // the top) and a click copies the log line. The
        // router keeps the pressed target wherever the pointer is released,
        // and `entry_at` matches on the row alone, so the body gates.
        MouseTarget::DebugConsole => {
            if !app
                .debug_console
                .body_area
                .contains((event.column, event.row).into())
            {
                return;
            }
            match event.kind {
                MouseEventKind::ScrollUp => crate::debug_console::wheel(app, client, true),
                MouseEventKind::ScrollDown => crate::debug_console::wheel(app, client, false),
                MouseEventKind::Up(MouseButton::Left) => {
                    crate::debug_console::copy_entry_at(app, event.row)
                }
                _ => {}
            }
        }
        MouseTarget::SubagentList => subagent_list_event(app, event),
        MouseTarget::Config | MouseTarget::ConfigEditor => {
            config::handle_mouse(app, client, config_tx, event)
        }
        // The wizard's regions never reach the loop (the wizard runs
        // pre-session and routes its own mouse events).
        MouseTarget::Blocked
        | MouseTarget::Completion
        | MouseTarget::OnboardingThemeList
        | MouseTarget::OnboardingPreview
        | MouseTarget::OnboardingLinks
        | MouseTarget::OnboardingInputs => {}
    }
}

/// The scrollable option list a mouse target shows, if any.
pub(crate) fn option_list(app: &mut App, target: MouseTarget) -> Option<&mut dyn OptionView> {
    Some(match target {
        MouseTarget::ThemePicker => &mut app.theme_picker.list,
        MouseTarget::ModelPicker => &mut app.model_picker.list,
        MouseTarget::ThinkingPicker => &mut app.thinking_picker.list,
        MouseTarget::LogLevelPicker => &mut app.log_level_picker.list,
        MouseTarget::ResumePicker => &mut app.resume_picker.list,
        MouseTarget::RemoteProject => &mut app.vibe_code_project.list,
        MouseTarget::Mcp => &mut app.mcp.list,
        MouseTarget::Plugins => &mut app.plugins.list,
        MouseTarget::Config => &mut app.config_screen.list,
        MouseTarget::ConfigEditor => &mut app.config_screen.edit.as_mut()?.list,
        MouseTarget::Question => &mut app.question_app.list,
        MouseTarget::SubagentList => &mut app.subagents.list.options,
        _ => return None,
    })
}

/// A completed click on an option of a single-action bottom-app list.
fn picked(
    app: &mut App,
    event: MouseEvent,
    list: fn(&mut App) -> &mut OptionList<SingleAction>,
) -> bool {
    option_gesture(app, event, RegionId::BottomApp, list).is_some()
}

/// A click on the highlighted option of a multi-action bottom-app list.
fn confirmed(
    app: &mut App,
    event: MouseEvent,
    list: fn(&mut App) -> &mut OptionList<MultiAction>,
) -> bool {
    let click = option_gesture(app, event, RegionId::BottomApp, list);
    matches!(click, Some(Click::Confirm(_)))
}

/// Drags select text; a press, and a release on the pressed cell, also click.
fn bottom_app_event(app: &mut App, event: MouseEvent, click: impl FnOnce(&mut App, MouseEvent)) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            click(app, event);
            selection::press_owned(app, at, RegionId::BottomApp);
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            let same_cell = app.selection.press == Some(at);
            let selected = matches!(selection::release(app), Release::Selected);
            if same_cell && !selected {
                click(app, event);
            }
        }
        _ => {}
    }
}

/// The subagent list: hover moves the marker and a click selects the row.
fn subagent_list_event(app: &mut App, event: MouseEvent) {
    let index = app
        .subagents
        .list
        .options
        .index_at((event.column, event.row));
    match event.kind {
        MouseEventKind::Moved => {
            app.subagents.list.mouse_session_id = index.map(|index| {
                crate::subagents::list::session_id_at(app, index)
                    .unwrap_or_else(|| crate::subagents::MAIN_SESSION_ID.to_owned())
            });
        }
        MouseEventKind::Down(MouseButton::Left) if index.is_some() => {
            app.subagents.list.focused = true;
            app.set_app_focus(false);
            crate::selection::blur_composer(app);
        }
        _ => {}
    }
    if let Some(index) = app.subagents.list.options.mouse(event) {
        let session_id = crate::subagents::list::session_id_at(app, index);
        crate::subagents::select(app, session_id);
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
            // The input box stays enabled while the list is focused, so
            // clicking the composer hands it focus.
            if target == MouseTarget::Composer && app.subagents.list.focused {
                crate::subagents::focus_input(app);
            }
            // The inline notice survives a transcript mouse-down
            // (it clears only via its own timeout or a queue-mode event).
            app.selection.had_selection_at_press =
                app.selection.region.is_some() || app.selection.bottom_bar.is_some();
            // A bottom app hides the composer but leaves its last rect behind, so
            // only a press routed to the painted composer may select in it.
            if target == MouseTarget::Composer {
                selection::press(app, at);
            } else {
                selection::press_owned(app, at, selection::RegionId::Main);
            }
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
    // With a selection showing, only the header row toggles.
    if app.selection.had_selection_at_press && header != Some(row) {
        return;
    }
    // The child view swaps its transcript and cache in only at render time,
    // so the expandability check and the layout cache to invalidate both
    // follow the viewed child, like `App::toggle_tools`.
    let lone = if let Some(child) = app
        .subagents
        .viewed_subagent_id
        .as_deref()
        .and_then(|child_id| app.subagents.transcripts.child_mut(child_id))
    {
        if !child.transcript.is_expandable(&id) {
            return;
        }
        child.cache.invalidate_layouts();
        child.transcript.group_of(&id).is_none()
    } else {
        if !app.view.transcript.is_expandable(&id) {
            return;
        }
        app.view.transcript_cache.invalidate_layouts();
        app.view.transcript.group_of(&id).is_none()
    };
    let reveal = !app.view.expanded.remove(&id);
    // A lone call carries its fold into the group a later call may open under its id.
    if lone && !id.starts_with(KEY_PREFIX) {
        let group_key = format!("{KEY_PREFIX}{id}");
        app.view.expanded.remove(&group_key);
        if reveal {
            app.view.expanded.insert(group_key);
        }
    }
    if reveal {
        app.view.expanded.insert(id.clone());
    }
    // A toggling click ends the chain, so the next click toggles again instead of selecting a word.
    app.selection.chain = Default::default();
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

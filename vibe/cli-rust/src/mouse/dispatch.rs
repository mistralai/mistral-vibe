//! Target-specific mouse behavior after routing and capture.

use std::sync::Arc;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use tokio::sync::mpsc;

use super::MouseTarget;
use crate::app::App;
use crate::config;
use crate::selection::{self, Release};
use crate::server::Client;
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
        MouseTarget::Approval => approval::handle_mouse(app, event),
        MouseTarget::Question => question_input::handle_mouse(app, event),
        MouseTarget::Mcp => match event.kind {
            MouseEventKind::Down(MouseButton::Left) => mcp::press(app, at),
            MouseEventKind::Up(MouseButton::Left) => mcp::release(app, client, at),
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
        MouseTarget::Config | MouseTarget::ConfigEditor => {
            config::handle_mouse(app, client, config_tx, event)
        }
        MouseTarget::Blocked
        | MouseTarget::Completion
        | MouseTarget::ThemePicker
        | MouseTarget::ModelPicker
        | MouseTarget::LogLevelPicker
        | MouseTarget::ResumePicker
        | MouseTarget::Rewind => {}
    }
}

/// Selecting toast text never dismisses the toast, which self-hides on timeout.
fn toast_event(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => selection::press_toast(app, at),
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            selection::release(app);
        }
        _ => {}
    }
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
            // Python keeps the inline notice across a transcript mouse-down
            // (it clears only via its own timeout or a queue-mode event).
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
                }
            } else if matches!(released, Release::RegionClick) {
                toggle_effect_at(app, event.row);
            }
        }
        _ => {}
    }
}

fn toggle_effect_at(app: &mut App, row: u16) {
    let Some(id) = app
        .view
        .entry_hitmap
        .iter()
        .find(|(top, bottom, _)| row >= *top && row < *bottom)
        .map(|(_, _, id)| id.clone())
    else {
        return;
    };
    if !app.view.transcript.is_expandable(&id) {
        return;
    }
    if !app.view.expanded.remove(&id) {
        app.view.expanded.insert(id);
    }
    app.view.transcript_cache.invalidate_layouts();
}

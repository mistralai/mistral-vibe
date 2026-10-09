//! Keyboard and mouse input for tool approvals.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, MouseEvent, MouseEventKind};

use crate::app::App;
use crate::question_app::input_grace_period;
use crate::server::{ApprovalDecisionType, Client};

const OPTION_COUNT: usize = 4;
const MOUSE_SCROLL_STEP: usize = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyAction {
    NavigateUp,
    NavigateDown,
    ScrollUp,
    ScrollDown,
    Submit,
    Choose(usize),
}

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key_action(&key) {
        Some(KeyAction::NavigateUp) => navigate(app, false),
        Some(KeyAction::NavigateDown) => navigate(app, true),
        Some(KeyAction::ScrollUp) => scroll_detail(app, false, app.approval.detail_viewport.max(1)),
        Some(KeyAction::ScrollDown) => {
            scroll_detail(app, true, app.approval.detail_viewport.max(1))
        }
        Some(KeyAction::Submit) => choose(app, client, app.approval.selected),
        Some(KeyAction::Choose(option)) => choose(app, client, option),
        None => {}
    }
}

pub fn key_action(key: &KeyEvent) -> Option<KeyAction> {
    if !key.modifiers.is_empty() {
        return None;
    }
    match key.code {
        KeyCode::Up | KeyCode::Char('k') => Some(KeyAction::NavigateUp),
        KeyCode::Down | KeyCode::Char('j') => Some(KeyAction::NavigateDown),
        KeyCode::PageUp => Some(KeyAction::ScrollUp),
        KeyCode::PageDown => Some(KeyAction::ScrollDown),
        KeyCode::Enter => Some(KeyAction::Submit),
        KeyCode::Char('y') => Some(KeyAction::Choose(0)),
        KeyCode::Char('n') | KeyCode::Esc => Some(KeyAction::Choose(OPTION_COUNT - 1)),
        _ => crate::list_nav::digit(key, OPTION_COUNT).map(KeyAction::Choose),
    }
}

pub fn handle_mouse(app: &mut App, event: MouseEvent) {
    match event.kind {
        MouseEventKind::ScrollUp => scroll_detail(app, false, MOUSE_SCROLL_STEP),
        MouseEventKind::ScrollDown => scroll_detail(app, true, MOUSE_SCROLL_STEP),
        _ => {}
    }
}

fn scroll_detail(app: &mut App, down: bool, amount: usize) {
    let max = app
        .approval
        .detail_rows
        .saturating_sub(app.approval.detail_viewport);
    app.approval.detail_scroll = if down {
        app.approval.detail_scroll.saturating_add(amount).min(max)
    } else {
        app.approval.detail_scroll.saturating_sub(amount)
    };
}

fn navigate(app: &mut App, down: bool) {
    app.approval.selected = crate::list_nav::wrap(app.approval.selected, OPTION_COUNT, down);
}

fn choose(app: &mut App, client: &Arc<Client>, option: usize) {
    if app.approval.responding
        || app
            .approval
            .mount_time
            .is_some_and(|at| at.elapsed() < input_grace_period())
    {
        return;
    }
    let decision = match option {
        0 => ApprovalDecisionType::Approve,
        1 => ApprovalDecisionType::ApproveForSession,
        2 => ApprovalDecisionType::ApprovePermanently,
        3 => ApprovalDecisionType::Deny,
        _ => return,
    };
    super::respond(app, client, decision);
    // Every deny is a rejection, whichever key sent it.
    if app.approval.responding && matches!(decision, ApprovalDecisionType::Deny) {
        crate::telemetry::user_cancelled_action(app, "reject_approval");
    }
}

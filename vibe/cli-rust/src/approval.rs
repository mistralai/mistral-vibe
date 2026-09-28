//! Tool-approval callback state and decisions.

mod input;
mod preview;
mod request;

use std::collections::VecDeque;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::sync::mpsc::Sender;

use crate::app::{App, Status, ToastSeverity};
use crate::server::{ApprovalCallback, ApprovalDecisionType, Client};

pub use input::{handle_key, handle_mouse, key_action, KeyAction};

const DEFAULT_TYPING_DEBOUNCE_MS: u64 = 1000;
const TYPING_DEBOUNCE_ENV_VAR: &str = "VIBE_TYPING_GRACE_PERIOD_MS";
pub const MAX_PENDING_APPROVALS: usize = 64;
const RECENT_CALLBACK_CAPACITY: usize = 256;
const RESPONSE_ERROR_TOAST_SECS: u64 = 5;

pub enum Event {
    Responded {
        callback_id: String,
    },
    Failed {
        callback_id: String,
        error: String,
    },
    Preview {
        callback_id: String,
        output: Option<crate::server::FileEditEffectOutput>,
    },
}

#[derive(Default)]
pub struct State {
    pub open: bool,
    pending: VecDeque<ApprovalCallback>,
    pub active: Option<ApprovalCallback>,
    pub selected: usize,
    pub mount_time: Option<Instant>,
    pub detail_scroll: usize,
    pub detail_rows: usize,
    pub detail_viewport: usize,
    pub detail_callback_id: Option<String>,
    pub detail_width: u16,
    pub detail_preview: Option<crate::server::FileEditEffectOutput>,
    responding: bool,
    recent_callback_ids: VecDeque<String>,
    pub tx: Option<Sender<Event>>,
}

impl State {
    pub fn enqueue(&mut self, callback: ApprovalCallback) -> Result<bool, Box<ApprovalCallback>> {
        let callback_id = callback.callback_id.as_str();
        let duplicate = self
            .active
            .as_ref()
            .is_some_and(|active| active.callback_id == callback_id)
            || self
                .pending
                .iter()
                .any(|pending| pending.callback_id == callback_id)
            || self
                .recent_callback_ids
                .iter()
                .any(|recent| recent == callback_id);
        if duplicate {
            return Ok(false);
        }
        if self.pending.len() >= MAX_PENDING_APPROVALS {
            return Err(Box::new(callback));
        }
        self.pending.push_back(callback);
        Ok(true)
    }

    pub fn pending_len(&self) -> usize {
        self.pending.len()
    }

    pub fn has_pending(&self) -> bool {
        !self.pending.is_empty()
    }

    pub fn has_capacity(&self) -> bool {
        self.pending.len() < MAX_PENDING_APPROVALS
    }

    fn finish_active(&mut self) -> Option<ApprovalCallback> {
        let callback = self.active.take()?;
        self.open = false;
        self.mount_time = None;
        self.detail_scroll = 0;
        self.detail_rows = 0;
        self.detail_viewport = 0;
        self.detail_callback_id = None;
        self.detail_width = 0;
        self.detail_preview = None;
        self.responding = false;
        self.recent_callback_ids
            .push_back(callback.callback_id.clone());
        if self.recent_callback_ids.len() > RECENT_CALLBACK_CAPACITY {
            self.recent_callback_ids.pop_front();
        }
        Some(callback)
    }
}

fn typing_debounce() -> Duration {
    let ms = std::env::var(TYPING_DEBOUNCE_ENV_VAR)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(DEFAULT_TYPING_DEBOUNCE_MS);
    Duration::from_millis(ms)
}

pub fn typing_pause_deadline(app: &App) -> Option<Instant> {
    if !can_wake(app) {
        return None;
    }
    app.approval.pending.front()?;
    Some(app.chat_input.last_keystroke? + typing_debounce())
}

pub fn can_wake(app: &App) -> bool {
    app.approval.active.is_none()
        && app.approval.has_pending()
        && matches!(
            app.session.status,
            Status::Ready | Status::Generating { .. }
        )
}

pub fn show_pending(app: &mut App) {
    if !can_wake(app) {
        return;
    }
    let Some(callback) = app.approval.pending.front() else {
        return;
    };
    let status = &callback.detail.effect.display.status_text;
    app.view
        .loading
        .begin_action_required(if status.is_empty() {
            "Waiting for approval"
        } else {
            status
        });
    if typing_pause_deadline(app).is_some_and(|deadline| Instant::now() < deadline) {
        return;
    }
    let Some(callback) = app.approval.pending.pop_front() else {
        return;
    };
    crate::terminal_notifier::action_required(app);
    app.approval.open = true;
    app.approval.active = Some(callback);
    app.approval.selected = 0;
    app.approval.mount_time = Some(Instant::now());
    app.approval.detail_scroll = 0;
    app.approval.detail_callback_id = None;
    app.approval.detail_preview = None;
    preview::load(app);
}

/// Consume one delivered approval, or return `false` so the event loop retains it.
pub fn on_callback_call(app: &mut App, params: &Value) -> bool {
    let Some(raw) = params.get("callback") else {
        return true;
    };
    if raw.pointer("/detail/kind").and_then(Value::as_str) != Some("approval") {
        return true;
    }
    let Ok(callback) = serde_json::from_value::<ApprovalCallback>(raw.clone()) else {
        tracing::warn!(?params, "approval callback is malformed");
        return true;
    };
    match app.approval.enqueue(callback) {
        Ok(true) => {
            show_pending(app);
            true
        }
        Ok(false) => true,
        Err(callback) => {
            tracing::debug!(
                callback_id = callback.callback_id,
                capacity = MAX_PENDING_APPROVALS,
                "approval backlog full; deferring callback delivery"
            );
            false
        }
    }
}

fn respond(app: &mut App, client: &Arc<Client>, decision: ApprovalDecisionType) {
    let Some(callback) = app.approval.active.as_ref() else {
        return;
    };
    app.approval.responding = request::send(app, client, callback, decision);
}

pub fn apply_event(app: &mut App, event: Event) {
    let callback_id = match &event {
        Event::Responded { callback_id }
        | Event::Failed { callback_id, .. }
        | Event::Preview { callback_id, .. } => callback_id,
    };
    if app
        .approval
        .active
        .as_ref()
        .is_none_or(|callback| callback.callback_id != *callback_id)
    {
        return;
    }
    match event {
        Event::Responded { .. } => {
            app.approval.finish_active();
            if app.approval.has_pending() {
                show_pending(app);
            } else {
                app.view.loading.end_action_required();
                crate::terminal_notifier::restore_running(app);
            }
        }
        Event::Failed { error, .. } => {
            app.approval.responding = false;
            app.show_toast(
                format!("Failed to answer approval: {error}"),
                ToastSeverity::Warning,
                RESPONSE_ERROR_TOAST_SECS,
            );
        }
        Event::Preview { output, .. } => {
            app.approval.detail_preview = output;
            app.approval.detail_callback_id = None;
        }
    }
}

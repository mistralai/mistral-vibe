//! `goal/set` and `goal/clear`; the goal itself comes back through `session/updated`.

use std::sync::Arc;

use super::Panel;
use crate::app::{App, Severity};
use crate::server::{method, Client, GoalClearParams, GoalSetParams, RpcError};

pub const CONFLICT_MESSAGE: &str = "The goal changed elsewhere; showing the latest.";
pub const UNSUPPORTED_MESSAGE: &str = "This app server does not support goals.";

/// A goal request's answer; success needs no work, the patch already moved the goal.
pub enum Event {
    Done,
    /// The notice, and the panel to reopen so a rejected edit keeps its text.
    Failed(Severity, String, Option<Panel>),
}

/// Send `goal/set` for the current session.
pub(super) fn set(app: &mut App, client: &Arc<Client>, params: GoalSetParams) {
    send_set(app, client, params, None);
}

/// Send `goal/set`; `panel` reopens if the server rejects it.
pub(super) fn send_set(
    app: &mut App,
    client: &Arc<Client>,
    mut params: GoalSetParams,
    panel: Option<Panel>,
) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    params.session_id = session_id;
    send(
        app,
        client,
        method::GOAL_SET,
        serde_json::to_value(params),
        panel,
    );
}

/// Send `goal/clear` for `goal_id`.
pub(super) fn clear(app: &mut App, client: &Arc<Client>, goal_id: String) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let params = GoalClearParams {
        session_id,
        expected_goal_id: goal_id,
    };
    let params = serde_json::to_value(params);
    send(app, client, method::GOAL_CLEAR, params, None);
}

fn send(
    app: &App,
    client: &Arc<Client>,
    method: &'static str,
    params: serde_json::Result<serde_json::Value>,
    panel: Option<Panel>,
) {
    let Ok(params) = params else {
        return;
    };
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        match client.request_err(method, params).await {
            Ok(_) => Event::Done,
            Err(error) => {
                let (severity, text) = failure_notice(&error);
                Event::Failed(severity, text, panel)
            }
        }
    });
}

/// Show why a goal request failed, and reopen a rejected edit of a goal that is
/// still there on its latest revision, unless another panel opened since.
pub fn apply_event(app: &mut App, event: Event) {
    let Event::Failed(severity, text, panel) = event else {
        return;
    };
    crate::ui::notice::show_sticky(app, severity, &text);
    let (Some(goal), None, Some(Panel::Edit { mut form, nested })) =
        (&app.goal.goal, &app.goal.panel, panel)
    else {
        return;
    };
    if form.goal_id == goal.goal_id {
        form.revision = goal.revision;
        app.goal.panel = Some(Panel::Edit { form, nested });
    }
}

/// The notice a failed goal request shows.
pub fn failure_notice(error: &RpcError) -> (Severity, String) {
    if error.is_method_not_found() {
        return (Severity::Warning, UNSUPPORTED_MESSAGE.to_owned());
    }
    if error.code() == "conflict" {
        return (Severity::Warning, CONFLICT_MESSAGE.to_owned());
    }
    let text = match error.message.is_empty() {
        true => format!("Goal request failed ({})", error.code()),
        false => error.message.clone(),
    };
    (Severity::Error, text)
}

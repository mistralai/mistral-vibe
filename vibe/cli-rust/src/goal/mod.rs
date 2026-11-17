//! `/goal`: the session goal the server works toward, rendered as a row and panels.

use std::sync::Arc;

use serde::Deserialize;
use serde_json::{json, Value};

use crate::app::{App, Severity};
use crate::option_list::{OptionList, SingleAction};
use crate::server::{Client, GoalSetParams, GoalStatus, PublicGoal};
use crate::terminal_notifier::NotificationContext;

pub mod edit;
pub mod format;
mod keys;
pub mod request;

pub use edit::EditForm;
pub use keys::{handle_key, handle_prompt_key, is_prompt_key, paste};
pub use request::{apply_event, Event};

pub const NO_GOAL: &str = "No goal set. Use /goal <objective>.";

/// Goal state: the server's goal, the open panel and the resume choice.
#[derive(Default)]
pub struct State {
    pub goal: Option<PublicGoal>,
    pub panel: Option<Panel>,
    /// The confirmation's two options.
    pub list: OptionList<SingleAction>,
    /// Offered once when an attach finds a goal the closed session suspended.
    pub resume_prompt: bool,
    /// A goal notification fired since the current turn started.
    notified: bool,
    /// The session and running turn `notified` belongs to.
    running: (Option<String>, Option<String>),
    /// The current turn runs a prompt the user sent, not a hidden continuation.
    user_turn: bool,
}

/// The goal bottom app; `nested` panels go back to the detail panel on Esc.
pub enum Panel {
    Detail,
    Edit { form: Box<EditForm>, nested: bool },
    Confirm(Confirm),
}

/// `goal_id` is the goal the confirmation opened on, so a goal that changed meanwhile is not touched.
pub struct Confirm {
    pub kind: ConfirmKind,
    pub goal_id: String,
    pub nested: bool,
}

pub enum ConfirmKind {
    Replace(String),
    Clear,
}

impl ConfirmKind {
    /// `(title, question, action)` the confirmation shows.
    pub fn text(&self) -> (&'static str, &'static str, &'static str) {
        match self {
            Self::Replace(_) => (
                "Replace goal",
                "Replace the current goal? Its progress is lost.",
                "Replace",
            ),
            Self::Clear => (
                "Clear goal",
                "Clear the goal? Its progress is lost.",
                "Clear",
            ),
        }
    }

    /// The option labels, the action first.
    pub fn options(&self) -> [&'static str; 2] {
        [self.text().2, "Cancel"]
    }
}

/// Adopt the goal of a session state; `attached` offers the resume choice for a suspended goal.
pub fn adopt(app: &mut App, goal: Option<Box<PublicGoal>>, attached: bool) {
    let goal = goal.map(|goal| *goal);
    let suspended = goal.as_ref().is_some_and(PublicGoal::suspended);
    app.goal.resume_prompt = suspended && (attached || app.goal.resume_prompt);
    if goal.is_none() {
        app.goal.panel = None;
    }
    app.goal.goal = goal;
}

/// Apply the `/goal` ops of a `session/updated` patch for the current session.
pub fn track_patch(app: &mut App, params: &Value) {
    let other = params["sessionId"].as_str();
    if other.is_some_and(|id| {
        app.session
            .session_id
            .as_deref()
            .is_some_and(|own| own != id)
    }) {
        return;
    }
    let ops: Vec<Value> = params["patch"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|op| {
            op["path"]
                .as_str()
                .is_some_and(|path| path == "/goal" || path.starts_with("/goal/"))
        })
        .cloned()
        .collect();
    if ops.is_empty() {
        return;
    }
    let mut doc = match &app.goal.goal {
        Some(goal) => json!({ "goal": goal }),
        None => json!({}),
    };
    crate::transcript::apply_json_patch(&mut doc, &ops);
    let goal = doc
        .get("goal")
        .and_then(|v| PublicGoal::deserialize(v).ok())
        .map(Box::new);
    let previous = app.goal.goal.as_ref().map(|goal| goal.status);
    adopt(app, goal, false);
    notify_transition(app, previous);
}

/// A goal that stopped on its own tells the user, like a finished turn.
fn notify_transition(app: &mut App, previous: Option<GoalStatus>) {
    let Some(status) = app.goal.goal.as_ref().map(|goal| goal.status) else {
        return;
    };
    if previous == Some(status) {
        return;
    }
    let message = match status {
        GoalStatus::Complete => "Goal complete",
        GoalStatus::Blocked => "Goal blocked",
        GoalStatus::BudgetLimited => "Goal budget reached",
        GoalStatus::UsageLimited => "Goal stopped: usage limit",
        _ => return,
    };
    app.goal.notified = true;
    let context = NotificationContext::Goal {
        message,
        complete: status == GoalStatus::Complete,
    };
    app.terminal_notifier.notify(context, crate::clock::now());
}

/// Turn `turn_id` starts; `user_turn` when it runs a prompt the user sent (it
/// has a queue item), as opposed to a goal continuation the server started.
pub fn turn_started(app: &mut App, turn_id: Option<String>, user_turn: bool) {
    let session = app.session.session_id.clone();
    running(app, session, turn_id);
    app.goal.user_turn = user_turn;
}

/// Re-read the running turn and whether it is the user's from the turns of
/// `session_id`'s state, so a snapshot, reconnect or session switch does not
/// keep flags that belong to another turn or session.
pub fn sync_running_turn(app: &mut App, session_id: &str, turns: Option<&[Value]>) {
    let turn = turns
        .unwrap_or_default()
        .iter()
        .find(|turn| turn["status"].as_str() == Some("in_progress"));
    let turn_id = turn.and_then(|turn| turn["id"].as_str()).map(str::to_owned);
    running(app, Some(session_id.to_owned()), turn_id);
    app.goal.user_turn = turn.is_some_and(|turn| turn["queueItemId"].as_str().is_some());
}

/// A notification fired for another turn or session must not silence this one.
fn running(app: &mut App, session: Option<String>, turn_id: Option<String>) {
    let running = (session, turn_id);
    if app.goal.running != running {
        app.goal.notified = false;
        app.goal.running = running;
    }
}

/// Whether a finished turn stays silent: a continuation of a goal that keeps
/// working, or a turn whose goal already notified. The user's own prompts
/// always tell them their answer landed.
pub fn quiet_turn_end(app: &App) -> bool {
    app.goal.notified || (!app.goal.user_turn && status(app) == Some(GoalStatus::Active))
}

pub fn status(app: &App) -> Option<GoalStatus> {
    app.goal.goal.as_ref().map(|goal| goal.status)
}

/// Run `/goal [<objective>|edit|pause|resume|clear]`.
pub fn command(app: &mut App, client: &Arc<Client>, args: &str) {
    let word = args.to_ascii_lowercase();
    if args.is_empty() || matches!(word.as_str(), "edit" | "pause" | "resume" | "clear") {
        let Some(goal) = app.goal.goal.clone() else {
            crate::ui::notice::show_sticky(app, Severity::Information, NO_GOAL);
            return;
        };
        match word.as_str() {
            "" => app.goal.panel = Some(Panel::Detail),
            "edit" => open_edit(app, &goal, false),
            "pause" => set_status(app, client, "paused"),
            "resume" => set_status(app, client, "active"),
            _ => clear(app, client, &goal, false),
        }
        return;
    }
    match app.goal.goal.clone() {
        None => request::set(app, client, objective_params(None, args)),
        Some(goal) if goal.status == GoalStatus::Complete => {
            request::set(app, client, objective_params(Some(goal.goal_id), args))
        }
        Some(goal) => confirm(
            app,
            ConfirmKind::Replace(args.to_owned()),
            goal.goal_id,
            false,
        ),
    }
}

/// A new goal; `replacing` names the goal it replaces.
fn objective_params(replacing: Option<String>, objective: &str) -> GoalSetParams {
    GoalSetParams {
        replace: replacing.is_some(),
        expected_goal_id: replacing,
        objective: Some(objective.to_owned()),
        ..Default::default()
    }
}

fn set_status(app: &mut App, client: &Arc<Client>, status: &'static str) {
    let params = GoalSetParams {
        status: Some(status),
        ..Default::default()
    };
    request::set(app, client, params);
}

fn open_edit(app: &mut App, goal: &PublicGoal, nested: bool) {
    let form = Box::new(EditForm::new(goal));
    app.goal.panel = Some(Panel::Edit { form, nested });
}

/// Clear at once a goal that is done; otherwise confirm first.
fn clear(app: &mut App, client: &Arc<Client>, goal: &PublicGoal, nested: bool) {
    if goal.status == GoalStatus::Complete {
        app.goal.panel = None;
        request::clear(app, client, goal.goal_id.clone());
        return;
    }
    confirm(app, ConfirmKind::Clear, goal.goal_id.clone(), nested);
}

/// Move the confirmation's highlight, wrapping.
pub fn step(app: &mut App, down: bool) {
    app.goal.list.step(down, 2, true, |_| true);
}

fn confirm(app: &mut App, kind: ConfirmKind, goal_id: String, nested: bool) {
    app.goal.list = OptionList::new(0);
    let confirm = Confirm {
        kind,
        goal_id,
        nested,
    };
    app.goal.panel = Some(Panel::Confirm(confirm));
}

/// Enter or a click on the confirmation: run its action, or go back on Cancel.
pub fn choose(app: &mut App, client: &Arc<Client>) {
    let Some(Panel::Confirm(confirm)) = app.goal.panel.take() else {
        return;
    };
    if app.goal.list.selected() != 0 {
        app.goal.panel = confirm.nested.then_some(Panel::Detail);
        return;
    }
    let goal_id = confirm.goal_id;
    match confirm.kind {
        ConfirmKind::Replace(objective) => {
            request::set(app, client, objective_params(Some(goal_id), &objective))
        }
        ConfirmKind::Clear => request::clear(app, client, goal_id),
    }
}

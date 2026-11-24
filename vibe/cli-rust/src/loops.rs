//! `/loop` with no arguments: the session's scheduled loops, to review, edit, run, delete and add.

use std::sync::Arc;

use serde_json::{json, Value};

use crate::app::App;
use crate::option_list::{MultiAction, OptionList};
use crate::server::{method, Client, PublicSessionState, ScheduledLoop};

mod form;
mod input;
mod run;
pub mod schedule;

pub use form::{Editing, FieldArea, Form, FormField, INSTRUCTIONS_ROWS};
pub use input::{edit, handle_key, paste};
pub use run::run;

/// The loop manager (a bottom app): the list, or the form that adds or edits a loop.
#[derive(Default)]
pub struct LoopsApp {
    pub open: bool,
    /// Highlighted loop and viewport; a row has several actions (Enter/`e`, `r`, `d`, `n`).
    pub list: OptionList<MultiAction>,
    /// The add or edit form, shown in place of the list while set.
    pub form: Option<Form>,
    /// The loop a `loops/delete` is removing.
    pub deleting: Option<String>,
    /// The loop a `loops/run` is making due.
    pub running: Option<String>,
    /// The last request's failure, shown above the footer until the next action.
    pub error: Option<String>,
    /// Row loop ids: soonest first on open, then frozen; new loops append.
    pub order: Vec<String>,
    /// The highlighted loop's instructions pane.
    pub details: Details,
    /// Counts sent `loops/create` and `loops/update`; only the open form's latest one may close it.
    pub submissions: u64,
}

/// The instructions pane's scroll over what it last showed.
#[derive(Debug, Default)]
pub struct Details {
    /// The loop it shows; another loop starts at the top.
    pub shown: Option<String>,
    pub scroll: usize,
    /// The instructions' lines and how many fit, as last rendered.
    pub lines: usize,
    pub visible: usize,
}

impl Details {
    /// Scroll by `amount` lines, stopping at either end.
    pub fn scroll_by(&mut self, down: bool, amount: usize) {
        let target = match down {
            true => self.scroll.saturating_add(amount),
            false => self.scroll.saturating_sub(amount),
        };
        self.scroll = target.min(self.lines.saturating_sub(self.visible));
    }
}

/// An answer to a `loops/*` request, with the session it was sent for.
#[derive(Debug)]
pub enum Event {
    Deleted {
        session_id: String,
        id: String,
        error: Option<String>,
    },
    Saved {
        session_id: String,
        /// The `LoopsApp::submissions` count that sent it.
        submission: u64,
        result: Result<ScheduledLoop, String>,
    },
    Ran {
        session_id: String,
        result: Result<ScheduledLoop, String>,
    },
}

/// Open the manager on the soonest loop.
pub fn open(app: &mut App) {
    let order = app
        .session
        .loops
        .iter()
        .map(|scheduled| scheduled.id.clone());
    app.loops = LoopsApp {
        open: true,
        order: order.collect(),
        submissions: app.loops.submissions,
        ..LoopsApp::default()
    };
}

/// The rows in display order.
pub fn rows(app: &App) -> Vec<&ScheduledLoop> {
    let loops = &app.session.loops;
    app.loops
        .order
        .iter()
        .filter_map(|id| loops.iter().find(|scheduled| &scheduled.id == id))
        .collect()
}

/// Close the manager and drop any unsaved form.
pub fn close(app: &mut App) {
    app.loops.open = false;
    app.loops.form = None;
    app.loops.error = None;
}

/// Follow the loops a session state reports.
pub fn adopt_state(app: &mut App, state: &PublicSessionState) {
    replace(app, state.session.loops.clone());
}

/// Follow a `session/updated` patch that replaces `/loops`.
pub fn track_patch(app: &mut App, patch: &Value) {
    let loops = crate::utils::json::replace_ops(patch)
        .filter(|(path, _)| *path == "/loops")
        .last()
        .and_then(|(_, value)| {
            crate::utils::json::lenient_opt(value.clone())
                .ok()
                .flatten()
        });
    if let Some(loops) = loops {
        replace(app, loops);
    }
}

/// Store the loops soonest first; the open list keeps its rows and highlight.
fn replace(app: &mut App, mut loops: Vec<ScheduledLoop>) {
    loops.sort_by(|a, b| a.next_fire_at.total_cmp(&b.next_fire_at));
    let highlighted = selected(app).map(|scheduled| scheduled.id.clone());
    let known = |id: &String| loops.iter().any(|scheduled| &scheduled.id == id);
    app.loops.order.retain(known);
    for scheduled in &loops {
        if !app.loops.order.contains(&scheduled.id) {
            app.loops.order.push(scheduled.id.clone());
        }
    }
    app.session.loops = loops;
    highlight(app, highlighted);
}

/// Keep the highlight on loop `id`, else on its row, within the list.
fn highlight(app: &mut App, id: Option<String>) {
    let order = &app.loops.order;
    let index = id
        .and_then(|id| order.iter().position(|row| *row == id))
        .unwrap_or(app.loops.list.selected())
        .min(order.len().saturating_sub(1));
    app.loops.list.highlight(index);
}

/// The highlighted loop, if any.
pub fn selected(app: &App) -> Option<&ScheduledLoop> {
    let id = app.loops.order.get(app.loops.list.selected())?;
    app.session
        .loops
        .iter()
        .find(|scheduled| &scheduled.id == id)
}

/// Move the highlight one row, wrapping at the ends.
pub fn navigate(app: &mut App, down: bool) {
    let count = app.loops.order.len();
    app.loops.list.step(down, count, true, |_| true);
}

/// Delete the highlighted loop at once.
pub fn delete(app: &mut App, client: &Arc<Client>) {
    let (Some(session_id), Some(scheduled)) = (app.session.session_id.clone(), selected(app))
    else {
        return;
    };
    if app.loops.deleting.is_some() {
        return;
    }
    let id = scheduled.id.clone();
    app.loops.error = None;
    let answered = id.clone();
    let params = json!({"sessionId": session_id, "loopId": id.clone()});
    let sent = request(app, client, method::LOOPS_DELETE, params, move |result| {
        Event::Deleted {
            session_id,
            id: answered,
            error: result.err(),
        }
    });
    if sent {
        app.loops.deleting = Some(id);
    }
}

/// Schedule the form's loop, or save it over the edited one; the form stays open until the server accepts it.
pub fn save(
    app: &mut App,
    client: &Arc<Client>,
    session_id: String,
    (method, params): (&'static str, Value),
) -> bool {
    app.loops.error = None;
    let submission = app.loops.submissions + 1;
    let sent = request(app, client, method, params, move |result| {
        let result = result.and_then(|value| {
            serde_json::from_value(value["loop"].clone()).map_err(|error| error.to_string())
        });
        Event::Saved {
            session_id,
            submission,
            result,
        }
    });
    if sent {
        app.loops.submissions = submission;
    }
    sent
}

/// Send `method` in the background; false when there is no event loop to answer.
fn request(
    app: &App,
    client: &Arc<Client>,
    method: &'static str,
    params: Value,
    answer: impl FnOnce(Result<Value, String>) -> Event + Send + 'static,
) -> bool {
    if app.events.is_none() {
        return false;
    }
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let result = client.request(method, params).await;
        answer(result.map_err(|error| crate::server::request_error::describe(&error)))
    });
    true
}

/// Apply a `loops/*` answer; one for another session, or for a form since closed, is dropped.
pub fn apply_event(app: &mut App, event: Event) {
    let (Event::Deleted { session_id, .. }
    | Event::Saved { session_id, .. }
    | Event::Ran { session_id, .. }) = &event;
    if app.session.session_id.as_deref() != Some(session_id.as_str()) {
        return;
    }
    match event {
        Event::Deleted { id, error, .. } => {
            if app.loops.deleting.as_deref() == Some(id.as_str()) {
                app.loops.deleting = None;
            }
            if let Some(error) = error {
                app.loops.error = Some(format!("Failed to delete loop: {error}"));
                return;
            }
            let loops = app
                .session
                .loops
                .iter()
                .filter(|scheduled| scheduled.id != id);
            replace(app, loops.cloned().collect());
        }
        Event::Saved {
            submission, result, ..
        } => {
            let pending = app.loops.form.as_ref().and_then(|form| form.submission);
            if pending != Some(submission) {
                return;
            }
            saved(app, result);
        }
        Event::Ran { result, .. } => run::ran(app, result),
    }
}

fn saved(app: &mut App, result: Result<ScheduledLoop, String>) {
    match result {
        Err(error) => {
            if let Some(form) = &mut app.loops.form {
                form.submission = None;
            }
            app.loops.error = Some(error);
        }
        Ok(saved) => {
            app.loops.form = None;
            let mut loops = app.session.loops.clone();
            loops.retain(|scheduled| scheduled.id != saved.id);
            loops.push(saved.clone());
            replace(app, loops);
            highlight(app, Some(saved.id));
        }
    }
}

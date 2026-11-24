//! `r` in the `/loop` manager: run the highlighted loop now, once the session is idle; it keeps its schedule.

use std::sync::Arc;

use serde_json::json;

use super::{replace, request, selected, Event};
use crate::app::App;
use crate::server::{method, Client, ScheduledLoop};

/// Ask the server to make the highlighted loop due now.
pub fn run(app: &mut App, client: &Arc<Client>) {
    let (Some(session_id), Some(scheduled)) = (app.session.session_id.clone(), selected(app))
    else {
        return;
    };
    if app.loops.running.is_some() {
        return;
    }
    let id = scheduled.id.clone();
    app.loops.error = None;
    let params = json!({"sessionId": session_id, "loopId": id.clone()});
    let sent = request(app, client, method::LOOPS_RUN, params, move |result| {
        let result = result.and_then(|value| {
            serde_json::from_value(value["loop"].clone()).map_err(|error| error.to_string())
        });
        Event::Ran { session_id, result }
    });
    if sent {
        app.loops.running = Some(id);
    }
}

/// Show the loop as due, or why it could not run.
pub(super) fn ran(app: &mut App, result: Result<ScheduledLoop, String>) {
    app.loops.running = None;
    match result {
        Err(error) => app.loops.error = Some(format!("Failed to run loop: {error}")),
        Ok(due) => {
            let loops = app.session.loops.iter().cloned();
            let loops = loops.map(|scheduled| match scheduled.id == due.id {
                true => due.clone(),
                false => scheduled,
            });
            replace(app, loops.collect());
        }
    }
}

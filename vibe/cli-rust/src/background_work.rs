//! The background work a session state reports, followed so the waiting loader can name it.

use crate::app::App;
use crate::server::PublicSessionState;

/// Follow the running background processes a session state reports, and what
/// a running wait waits for.
pub fn adopt_state(app: &mut App, state: &PublicSessionState) {
    app.session.running_processes = state
        .background_processes
        .iter()
        .filter(|process| process.status == "running")
        .count();
    sync_wait_target(app);
}

/// `1 subagent`, `2 subagents`.
fn counted(count: usize, one: &str, many: &str) -> String {
    format!("{count} {}", if count == 1 { one } else { many })
}

/// What a running `wait` waits for, named from the subagents and background
/// tasks still running: `2 subagents and 1 background task`. `None` when
/// neither runs, so the generic wording stays.
fn wait_target(app: &App) -> Option<String> {
    let subagents = app
        .subagents
        .sessions
        .iter()
        .filter(|child| crate::subagents::is_active(child.status))
        .count();
    let tasks = app.session.running_processes;
    let parts: Vec<String> = [
        (subagents > 0).then(|| counted(subagents, "subagent", "subagents")),
        (tasks > 0).then(|| counted(tasks, "background task", "background tasks")),
    ]
    .into_iter()
    .flatten()
    .collect();
    match parts.as_slice() {
        [] => None,
        [one] => Some(one.clone()),
        [first, second] => Some(format!("{first} and {second}")),
        _ => unreachable!("two kinds at most"),
    }
}

/// Keep what a running `wait` waits for, so the loader reads it without counting per frame.
pub fn sync_wait_target(app: &mut App) {
    app.session.wait_target = wait_target(app);
}

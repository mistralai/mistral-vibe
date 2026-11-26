//! The background work a session state reports, followed so the waiting loader can name it.

use crate::app::App;
use crate::server::PublicSessionState;

/// Move onto `state`'s session with the background processes, loops and subagents it owns.
pub fn adopt_session(app: &mut App, state: &PublicSessionState) {
    app.set_session_id(state.session.id.clone());
    app.subagents.seed_snapshot(state.child_sessions.clone());
    adopt_state(app, state);
    crate::loops::adopt_state(app, state);
}

/// Follow the running background processes a session state reports, and what
/// a running wait waits for.
pub fn adopt_state(app: &mut App, state: &PublicSessionState) {
    app.session.background_tasks = state
        .background_processes
        .iter()
        .filter(|process| process.status == "running")
        .cloned()
        .collect();
    crate::tasks_panel::sync(app);
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
    let tasks = app.session.background_tasks.len();
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

/// Whether the running turn only waits for its background work; a program's sleep does not count.
pub fn only_waiting(app: &App) -> bool {
    app.session.shell_operation_id.is_none()
        && app
            .session
            .active_turn_id
            .as_deref()
            .is_some_and(|turn| app.view.transcript.only_model_waiting(turn))
}

/// Whether a message sent now steers an agent that only waits, so the empty composer says so; a selected queued prompt (editing implies it) takes the keys instead.
pub fn steerable(app: &App) -> bool {
    only_waiting(app)
        && !app.queue.paused
        && app.queue.selected.is_none()
        && crate::input::composer_reachable(app)
}

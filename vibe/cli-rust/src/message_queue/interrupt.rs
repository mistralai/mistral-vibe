//! Interrupting a turn the server has not started yet.

use std::time::Duration;

use crate::app::App;

/// How long an interrupt waits for a pending turn to start.
const START_WAIT: Duration = Duration::from_secs(30);

/// Whether an interrupt is waiting for the next turn to start.
pub fn interrupt_pending(app: &App) -> bool {
    app.queue
        .interrupt_on_start
        .is_some_and(|at| crate::clock::since(at) < START_WAIT)
}

/// Interrupt the next turn as soon as the server starts it.
pub fn interrupt_on_start(app: &mut App) {
    app.queue.interrupt_on_start = Some(crate::clock::now());
}

/// Whether the turn that just started is one the user already interrupted.
pub fn take_interrupt_on_start(app: &mut App) -> bool {
    app.queue
        .interrupt_on_start
        .take()
        .is_some_and(|at| crate::clock::since(at) < START_WAIT)
}

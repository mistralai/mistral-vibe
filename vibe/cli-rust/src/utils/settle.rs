//! Coalesce a channel's queued burst until it settles.

use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::time::Duration;

/// Collect `first` and every value that keeps arriving within `quiet` of the
/// last one, but never wait longer than `cap` past the burst's first value.
/// One call returns one settled burst: callers apply once per burst instead
/// of once per value.
pub fn drain_settled<T>(events: &Receiver<T>, first: T, quiet: Duration, cap: Duration) -> Vec<T> {
    let start = crate::clock::real_now();
    let mut burst = vec![first];
    loop {
        match events.recv_timeout(quiet) {
            Ok(value) => {
                burst.push(value);
                if crate::clock::real_now().duration_since(start) >= cap {
                    return burst;
                }
            }
            // A quiet window, or a dropped sender: the burst has settled.
            Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => return burst,
        }
    }
}

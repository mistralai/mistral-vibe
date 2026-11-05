//! Replay-harness input: numbered fences the client acks once settled, and steps of the frozen UI clock.

use std::io::Write;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::LazyLock;

use crossterm::event::{Event, KeyCode, KeyEventKind};

/// Kitty F24, sent after a step's keys on the same input stream.
const FENCE_KEY: KeyCode = KeyCode::F(24);
/// Kitty F23: advance the frozen UI clock by one step.
const ADVANCE_KEY: KeyCode = KeyCode::F(23);
const ADVANCE_STEP: std::time::Duration = std::time::Duration::from_millis(100);

/// Outside replay nothing is counted, owed or acked.
static ACTIVE: LazyLock<bool> = LazyLock::new(crate::utils::is_replaying);
static SEEN: AtomicI64 = AtomicI64::new(0);
/// Startup is fence 0, owed before the harness sends anything.
static ACKED: AtomicI64 = AtomicI64::new(-1);

/// Count `event` if it is a fence, or step the clock; true for the harness keys, which no surface handles.
pub fn observe(event: &Event) -> bool {
    if !*ACTIVE {
        return false;
    }
    match event {
        Event::Key(key) if key.code == FENCE_KEY => {
            if key.kind == KeyEventKind::Press {
                note();
            }
            true
        }
        Event::Key(key) if key.code == ADVANCE_KEY => {
            if key.kind == KeyEventKind::Press {
                crate::clock::advance(ADVANCE_STEP);
            }
            true
        }
        // A resize shares no stream with a key, so its own event is its fence.
        Event::Resize(..) => {
            note();
            false
        }
        _ => false,
    }
}

/// Count one fence: the fence key, a resize, or the replay server's `replay/fence`.
pub fn note() {
    SEEN.fetch_add(1, Ordering::SeqCst);
}

/// Whether startup or a counted fence still waits for its settled frame.
pub fn owed() -> bool {
    *ACTIVE && SEEN.load(Ordering::SeqCst) > ACKED.load(Ordering::SeqCst)
}

/// Ack the owed fences once `settled` says the drawn frame is final; each surface names why.
pub fn ack_if(settled: impl FnOnce() -> bool) {
    if owed() && settled() {
        ack();
    }
}

/// Ack every fence counted so far with `OSC 5379;vibe-idle;<count>`.
fn ack() {
    let seen = SEEN.load(Ordering::SeqCst);
    ACKED.store(seen, Ordering::SeqCst);
    let mut out = std::io::stdout();
    let _ = write!(out, "\x1b]5379;vibe-idle;{seen}\x07");
    let _ = out.flush();
}

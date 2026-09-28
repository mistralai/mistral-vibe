//! Multi-click chain tracking, Python `_update_click_chain`.

use std::sync::OnceLock;
use std::time::{Duration, Instant};

/// Python `App.CLICK_CHAIN_TIME_THRESHOLD`: how long a click chain stays alive.
const CLICK_CHAIN_TIME_THRESHOLD: Duration = Duration::from_millis(500);
const DOUBLE_CLICK: u8 = 2;
const TRIPLE_CLICK: u8 = 3;

/// Replay drives presses at machine speed, so a wall-clock window would make the
/// chain depend on load. The harness holds it open and relies on the same-cell
/// test alone; mirrors `_click_chain_threshold` in the Python client.
fn threshold() -> Duration {
    static THRESHOLD: OnceLock<Duration> = OnceLock::new();
    *THRESHOLD.get_or_init(|| match std::env::var_os("VIBE_REPLAY_FIXTURE") {
        Some(_) => Duration::MAX,
        None => CLICK_CHAIN_TIME_THRESHOLD,
    })
}

/// How much text one press selects (Python `SelectGranularity`).
#[derive(Clone, Copy, PartialEq, Eq, Default)]
pub enum Granularity {
    #[default]
    Char,
    Word,
    Paragraph,
}

/// Consecutive presses on the same cell, cycling char -> word -> paragraph.
#[derive(Default)]
pub struct ClickChain {
    count: u8,
    /// A triple click ends the cycle: the next press wraps back to a single one.
    consumed: bool,
    last_at: Option<Instant>,
    last_target: Option<(u16, u16)>,
}

impl ClickChain {
    /// Advance the chain for a press on `target` and return its granularity.
    pub fn press(&mut self, target: (u16, u16), now: Instant) -> Granularity {
        let within = self.last_target == Some(target)
            && self
                .last_at
                .is_some_and(|at| now.duration_since(at) <= threshold());
        self.count = if within && !self.consumed {
            self.count.saturating_add(1)
        } else {
            1
        };
        self.consumed = self.count >= TRIPLE_CLICK;
        self.last_at = Some(now);
        self.last_target = Some(target);
        match self.count {
            n if n >= TRIPLE_CLICK => Granularity::Paragraph,
            DOUBLE_CLICK => Granularity::Word,
            _ => Granularity::Char,
        }
    }

    /// A completed drag ends the chain so the next click starts a fresh one
    /// (Python `_on_mouse_up`: `if self._dragged`).
    pub fn release(&mut self, dragged: bool) {
        if dragged {
            *self = Self::default();
        }
    }
}

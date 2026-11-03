//! Multi-click chain tracking.

use std::time::Duration;

use crate::clock::UiInstant;

/// How long a click chain stays alive.
const CLICK_CHAIN_TIME_THRESHOLD: Duration = Duration::from_millis(500);
const DOUBLE_CLICK: u8 = 2;
const TRIPLE_CLICK: u8 = 3;

/// How much text one press selects.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
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
    last_at: Option<UiInstant>,
    last_target: Option<(u16, u16)>,
}

impl ClickChain {
    /// Advance the chain for a press on `target` and return its granularity.
    pub fn press(&mut self, target: (u16, u16), now: UiInstant) -> Granularity {
        let within = self.last_target == Some(target)
            && self
                .last_at
                .is_some_and(|at| now.duration_since(at) <= CLICK_CHAIN_TIME_THRESHOLD);
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

    /// A completed drag ends the chain so the next click starts a fresh one.
    pub fn release(&mut self, dragged: bool) {
        if dragged {
            *self = Self::default();
        }
    }
}

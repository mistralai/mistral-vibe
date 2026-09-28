//! The animated braille cat: a 26-step transition cycle with random pauses.

mod frames;

use std::collections::HashSet;
use std::time::{Duration, Instant};

use frames::{STARTING_DOTS, TRANSITIONS};

use crate::ui::braille_renderer;
use crate::ui::rng::Rng;

const WIDTH: i16 = 22;
const HEIGHT: i16 = 12;
pub const FRAME_INTERVAL: Duration = Duration::from_millis(160);
const CYCLE_DELAY_MIN_S: f64 = 5.0;
const CYCLE_DELAY_MAX_S: f64 = 20.0;
const MID_CYCLE_PAUSE_CHANCE: f64 = 0.25;
/// Transition indices where the cat may rest with eyes open.
const EYES_OPEN_PAUSE_FRAMES: [usize; 4] = [5, 11, 21, 24];

/// Animated cat state, advancing one transition per `tick`.
pub struct PetitChat {
    dots: HashSet<(i16, i16)>,
    transition_index: usize,
    /// After a pause, play a full cycle back to this frame before pausing again.
    resume_frame: Option<usize>,
    pause_until: Option<Instant>,
    rng: Rng,
}

impl Default for PetitChat {
    fn default() -> Self {
        let mut dots = HashSet::new();
        for (y, row) in STARTING_DOTS.iter().enumerate() {
            for &x in *row {
                dots.insert((x, y as i16));
            }
        }
        Self {
            dots,
            transition_index: 0,
            resume_frame: None,
            pause_until: None,
            rng: Rng::default(),
        }
    }
}

impl PetitChat {
    /// Advance one frame. Returns whether its visible dots changed.
    pub fn tick(&mut self, now: Instant) -> bool {
        if let Some(until) = self.pause_until {
            if now < until {
                return false;
            }
            self.pause_until = None;
        }

        let transition = &TRANSITIONS[self.transition_index];
        let mut changed = false;
        for dot in transition.remove {
            changed |= self.dots.remove(dot);
        }
        for &dot in transition.add {
            changed |= self.dots.insert(dot);
        }
        self.transition_index = (self.transition_index + 1) % TRANSITIONS.len();

        if !self.may_stop() {
            return changed;
        }
        let eyes_open = EYES_OPEN_PAUSE_FRAMES.contains(&self.transition_index);
        if self.transition_index == 0 || (eyes_open && self.rng.random() < MID_CYCLE_PAUSE_CHANCE) {
            self.pause_between_cycles(now);
        }
        changed
    }

    /// The current frame as a braille grid (`\n`-joined rows).
    pub fn render(&self) -> String {
        braille_renderer::render_braille(&self.dots, WIDTH, HEIGHT)
    }

    /// After a stop, play one full cycle back to the stop frame (`_may_stop`).
    fn may_stop(&mut self) -> bool {
        match self.resume_frame {
            None => true,
            Some(frame) if self.transition_index == frame => {
                self.resume_frame = None;
                true
            }
            Some(_) => false,
        }
    }

    fn pause_between_cycles(&mut self, now: Instant) {
        self.resume_frame = Some(self.transition_index);
        let delay = self.rng.uniform(CYCLE_DELAY_MIN_S, CYCLE_DELAY_MAX_S);
        self.pause_until = Some(now + Duration::from_secs_f64(delay));
    }
}

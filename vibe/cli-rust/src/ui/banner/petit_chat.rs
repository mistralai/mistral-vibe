//! The animated braille cat: a 26-step transition cycle with random pauses.

mod frames;

use std::collections::HashSet;
use std::time::{Duration, Instant};

use frames::{LECHONK_STARTING_DOTS, LECHONK_TRANSITIONS, STARTING_DOTS, TRANSITIONS};

use crate::ui::braille_renderer;
use crate::ui::rng::Rng;

const WIDTH: i16 = 22;
const HEIGHT: i16 = 12;
const LECHONK_WIDTH: i16 = 26;
const LECHONK_HEIGHT: i16 = 15;
pub const FRAME_INTERVAL: Duration = Duration::from_millis(160);
const CYCLE_DELAY_MIN_S: f64 = 5.0;
const CYCLE_DELAY_MAX_S: f64 = 20.0;
const MID_CYCLE_PAUSE_CHANCE: f64 = 0.25;
/// Transition indices where the cat may rest with eyes open.
const EYES_OPEN_PAUSE_FRAMES: [usize; 4] = [5, 11, 21, 24];

/// Which cat sprite to animate.
#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub enum CatVariant {
    /// The classic petit chat (22×12).
    #[default]
    LeChat,
    /// The chonkier pixilart cat (26×15), shown for Mistral Large / ml4 models.
    LeChonk,
}

impl CatVariant {
    /// Pick the variant from a model display name or alias.
    /// "ml4", "mistral large" (or any "mistral-large-*" API alias),
    /// "le-chaton-fat", "le-gros-chaton", "le-chonk" → LeChonk.
    /// Anything else → LeChat.
    pub fn from_model(model: &str) -> Self {
        let lower = model.to_lowercase();
        // Normalize hyphens to spaces so "mistral large" catches both
        // "Mistral Large" and "mistral-large-latest" / "mistral-large-4" etc.
        let normalized = lower.replace('-', " ");
        if normalized.contains("ml4")
            || normalized.contains("mistral large")
            || lower.contains("le-chaton-fat")
            || lower.contains("le-gros-chaton")
            || lower.contains("le-chonk")
        {
            CatVariant::LeChonk
        } else {
            CatVariant::LeChat
        }
    }

    fn starting_dots(&self) -> &'static [&'static [i16]] {
        match self {
            CatVariant::LeChat => &STARTING_DOTS,
            CatVariant::LeChonk => &LECHONK_STARTING_DOTS,
        }
    }

    fn transitions(&self) -> &'static [frames::Transition] {
        match self {
            CatVariant::LeChat => TRANSITIONS,
            CatVariant::LeChonk => LECHONK_TRANSITIONS,
        }
    }

    fn width(&self) -> i16 {
        match self {
            CatVariant::LeChat => WIDTH,
            CatVariant::LeChonk => LECHONK_WIDTH,
        }
    }

    fn height(&self) -> i16 {
        match self {
            CatVariant::LeChat => HEIGHT,
            CatVariant::LeChonk => LECHONK_HEIGHT,
        }
    }
}

/// Animated cat state, advancing one transition per `tick`.
pub struct PetitChat {
    dots: HashSet<(i16, i16)>,
    transition_index: usize,
    /// After a pause, play a full cycle back to this frame before pausing again.
    resume_frame: Option<usize>,
    pause_until: Option<Instant>,
    rng: Rng,
    variant: CatVariant,
}

impl Default for PetitChat {
    fn default() -> Self {
        let variant = CatVariant::LeChat;
        let mut dots = HashSet::new();
        for (y, row) in variant.starting_dots().iter().enumerate() {
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
            variant,
        }
    }
}

impl PetitChat {
    /// Switch to a different cat variant, resetting the animation.
    pub fn set_variant(&mut self, variant: CatVariant) {
        if self.variant == variant {
            return;
        }
        self.variant = variant;
        self.dots.clear();
        for (y, row) in variant.starting_dots().iter().enumerate() {
            for &x in *row {
                self.dots.insert((x, y as i16));
            }
        }
        self.transition_index = 0;
        self.resume_frame = None;
        self.pause_until = None;
    }

    /// Advance one frame. Returns whether its visible dots changed.
    pub fn tick(&mut self, now: Instant) -> bool {
        if let Some(until) = self.pause_until {
            if now < until {
                return false;
            }
            self.pause_until = None;
        }

        let transitions = self.variant.transitions();
        let transition = &transitions[self.transition_index];
        let mut changed = false;
        for dot in transition.remove {
            changed |= self.dots.remove(dot);
        }
        for &dot in transition.add {
            changed |= self.dots.insert(dot);
        }
        self.transition_index = (self.transition_index + 1) % transitions.len();

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
        braille_renderer::render_braille(&self.dots, self.variant.width(), self.variant.height())
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
